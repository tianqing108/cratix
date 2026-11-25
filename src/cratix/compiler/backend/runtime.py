"""AOT 生成的 C 运行时头 ✅（作为字符串随 .c 一起写出 ✅）。

只依赖 CPython 公开 C-API ✅ —— 不碰内部头 ✅（跨版本更稳 ✅）。
"""

from __future__ import annotations

HEADER_NAME = "cratix_runtime.h"

RUNTIME_H = r"""
/* cratix AOT 运行时头 ✅ —— 由 cratix 生成，请勿手改 ✅ */
#ifndef CRATIX_RUNTIME_H
#define CRATIX_RUNTIME_H

#include <Python.h>

/* 名字查找：模块全局 -> 内建 ✅
 * 返回**新引用** ✅；失败返回 NULL 且异常已设置 ✅。
 * 局部变量在生成的 C 里是 C 局部 ✅ —— 不走这里 ✅。 */
static PyObject *cratix_load_name(PyObject *globals, PyObject *builtins, const char *key) {
    PyObject *k;
    PyObject *v;
    if (globals != NULL) {
        k = PyUnicode_InternFromString(key);
        if (k == NULL) return NULL;
        v = PyDict_GetItemWithError(globals, k);
        Py_DECREF(k);
        if (v != NULL) {
            Py_INCREF(v);
            return v;
        }
        if (PyErr_Occurred()) return NULL;
    }
    if (builtins != NULL) {
        k = PyUnicode_InternFromString(key);
        if (k == NULL) return NULL;
        v = PyDict_GetItemWithError(builtins, k);
        Py_DECREF(k);
        if (v != NULL) {
            Py_INCREF(v);
            return v;
        }
        if (PyErr_Occurred()) return NULL;
    }
    PyErr_Format(PyExc_NameError, "name '%s' is not defined", key);
    return NULL;
}

/* 名字查找（**静态键版** ✅）：模块全局 -> 内建 ✅
 * 返回**新引用** ✅；失败返回 NULL 且异常已设置 ✅。
 * 与 `cratix_load_name` 的区别 ✅：键是模块 init 里 intern 好的 ✅（不再每次 InternFromString ✅）。 */
static PyObject *cratix_load_key(PyObject *globals, PyObject *builtins, PyObject *key, const char *name) {
    PyObject *v;
    if (globals != NULL) {
        v = PyDict_GetItemWithError(globals, key);
        if (v != NULL) {
            Py_INCREF(v);
            return v;
        }
        if (PyErr_Occurred()) return NULL;
    }
    if (builtins != NULL) {
        v = PyDict_GetItemWithError(builtins, key);
        if (v != NULL) {
            Py_INCREF(v);
            return v;
        }
        if (PyErr_Occurred()) return NULL;
    }
    PyErr_Format(PyExc_NameError, "name '%s' is not defined", name);
    return NULL;
}

/* 真值语义 ✅（与 CPython 一致 ✅） */
/* **`from m import *`** ✅ —— 按 CPython 的规则把名字搬进 globals ✅：
   有 `__all__` 就用它 ✅；否则跳过下划线开头的名字 ✅。 */
static int cratix_import_star(PyObject *module, PyObject *globals) {
    PyObject *all = PyObject_GetAttrString(module, "__all__");
    PyObject *source = NULL;
    PyObject *names = NULL;
    int result = -1;
    if (all != NULL && PySequence_Check(all)) {
        names = PySequence_List(all);
        if (names == NULL) { Py_DECREF(all); return -1; }
        Py_DECREF(all);
    } else {
        PyErr_Clear();
        Py_XDECREF(all);
        source = PyModule_GetDict(module);
        if (source == NULL) return -1;
        names = PyDict_Keys(source);
        if (names == NULL) return -1;
    }
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(names); i++) {
        PyObject *key = PyList_GET_ITEM(names, i);
        const char *text = PyUnicode_AsUTF8(key);
        if (text == NULL) goto done;
        if (source != NULL && text[0] == '_') continue;
        PyObject *value = PyObject_GetAttr(module, key);
        if (value == NULL) goto done;
        if (PyDict_SetItem(globals, key, value) < 0) { Py_DECREF(value); goto done; }
        Py_DECREF(value);
    }
    result = 0;
done:
    Py_XDECREF(names);
    return result;
}

/* **共享的空元组** ✅ —— `*rest` 没收到东西时不必每次分配 ✅（不可变 ✅ 可安全共享 ✅）。 */
static PyObject *cratix_empty_tuple(void) {
    static PyObject *cached = NULL;
    if (cached == NULL) {
        cached = PyTuple_New(0);
        if (cached == NULL) return NULL;
    }
    return Py_NewRef(cached);
}

static int cratix_truth(PyObject *o) {
    int r = PyObject_IsTrue(o);
    if (r < 0) {
        PyErr_Clear();
        return 0;
    }
    return r;
}

/* 格式说明符字符串 ✅（f-string 的 format_spec ✅） */
static PyObject *cratix_format(PyObject *value, PyObject *spec) {
    PyObject *out;
    if (spec == NULL) {
        out = PyObject_Str(value);
    } else {
        out = PyObject_Format(value, spec);
    }
    return out;
}

/* 空字符串 ✅（f-string 的分隔符 ✅ —— 返回**新引用** ✅） */
static PyObject *cratix_empty_str(void) {
    return PyUnicode_FromStringAndSize("", 0);
}

/* 异常对象构造 ✅（raise 的值可能是类 ✅ 也可能是实例 ✅） */
static int cratix_raise(PyObject *value) {
    PyObject *inst;
    if (value == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "No active exception to reraise");
        return -1;
    }
    if (PyType_Check(value) && PyType_IsSubtype((PyTypeObject *)value, (PyTypeObject *)PyExc_BaseException)) {
        inst = PyObject_CallNoArgs(value);
        if (inst == NULL) return -1;
        PyErr_SetObject((PyObject *)Py_TYPE(inst), inst);
        Py_DECREF(inst);
        return -1;
    }
    if (!PyExceptionInstance_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "exceptions must derive from BaseException");
        return -1;
    }
    PyErr_SetObject((PyObject *)Py_TYPE(value), value);
    return -1;
}

#endif /* CRATIX_RUNTIME_H */
"""