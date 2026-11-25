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

/* 真值语义 ✅（与 CPython 一致 ✅） */
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