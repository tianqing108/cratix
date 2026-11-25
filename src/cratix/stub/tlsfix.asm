; tlsfix.asm -- helpers returning the per-thread TLS table for patched accesses.
;
; The patched access site (MSVC x64) is:
;     mov  ecx, [rip+X]        ; module's own TLS index (runtime writable)
;     mov  rax, gs:[0x58]      ; thread's TLS pointer array (loader owned)
;     mov  rax, [rax+rcx*8]    ; per-thread block of that module
;
; We replace the gs:[0x58] load with:
;     call cratix_tls_asm_<dst>  ; helper: returns OUR table in <dst>
;     nop nop nop nop            ; keep the instruction length (9 bytes)
;
; The helper MUST behave like the replaced instruction: it may clobber ONLY
; the destination register (the original instruction clobbered only it).
; Everything else (including rax) is preserved.
;
; Flags: the C helper clobbers them -> pushfq/popfq restores them.
; Diagnostics: the flags seen before the call and after the call are copied
; into g_tlsfix_saved_flags / g_tlsfix_flags_after (stack slot corruption
; would show up as a difference).
;
; Stack parity: 9 slots are pushed before the C call, so the C function sees
; rsp = 8 (mod 16) as the x64 ABI requires.
;
; Keep this file ASCII-only: ml64 mis-parses non-ASCII comments.

EXTERN cratix_tls_table_c:PROC
EXTERN g_tlsfix_saved_flags:QWORD
EXTERN g_tlsfix_flags_after:QWORD

_TEXT SEGMENT

cratix_tls_asm_0 PROC
    push    rax
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rax
    ret
cratix_tls_asm_0 ENDP

cratix_tls_asm_1 PROC
    push    rcx
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rcx
    ret
cratix_tls_asm_1 ENDP

cratix_tls_asm_2 PROC
    push    rdx
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rdx
    ret
cratix_tls_asm_2 ENDP

cratix_tls_asm_3 PROC
    push    rbx
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rbx
    ret
cratix_tls_asm_3 ENDP

cratix_tls_asm_4 PROC
    push    rsp
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rsp
    ret
cratix_tls_asm_4 ENDP

cratix_tls_asm_5 PROC
    push    rbp
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rbp
    ret
cratix_tls_asm_5 ENDP

cratix_tls_asm_6 PROC
    push    rsi
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rsi
    ret
cratix_tls_asm_6 ENDP

cratix_tls_asm_7 PROC
    push    rdi
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     rdi
    ret
cratix_tls_asm_7 ENDP

cratix_tls_asm_8 PROC
    push    r8
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r8
    ret
cratix_tls_asm_8 ENDP

cratix_tls_asm_9 PROC
    push    r9
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r9
    ret
cratix_tls_asm_9 ENDP

cratix_tls_asm_10 PROC
    push    r10
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r10
    ret
cratix_tls_asm_10 ENDP

cratix_tls_asm_11 PROC
    push    r11
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r11
    ret
cratix_tls_asm_11 ENDP

cratix_tls_asm_12 PROC
    push    r12
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r12
    ret
cratix_tls_asm_12 ENDP

cratix_tls_asm_13 PROC
    push    r13
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r13
    ret
cratix_tls_asm_13 ENDP

cratix_tls_asm_14 PROC
    push    r14
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r14
    ret
cratix_tls_asm_14 ENDP

cratix_tls_asm_15 PROC
    push    r15
    push    rax
    push    rcx
    push    rdx
    push    r8
    push    r9
    push    r10
    push    r11
    pushfq
    mov     rax, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_saved_flags], rax
    call    cratix_tls_table_c
    mov     r11, qword ptr [rsp]
    mov     QWORD PTR [g_tlsfix_flags_after], r11
    mov     qword ptr [rsp+40h], rax
    popfq
    pop     r11
    pop     r10
    pop     r9
    pop     r8
    pop     rdx
    pop     rcx
    pop     rax
    pop     r15
    ret
cratix_tls_asm_15 ENDP

_TEXT ENDS
END
