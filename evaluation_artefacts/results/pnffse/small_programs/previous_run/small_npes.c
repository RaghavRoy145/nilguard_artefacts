/*
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

#include <assert.h>
#include <stdlib.h>
#include <stdnoreturn.h>
#include <stdio.h>
#include <string.h>

/* ========================================================================= */
/* GROUP 1: BASELINE & LOCAL NPEs (Tool works well here)                     */
/* ========================================================================= */

// BUG_TYPE: B-INTRA
// EXPECTED_STRATEGY: REPLACE (or SKIP/EVADE if return issue fixed)
// CURRENT_STATUS: Works as expected
int* malloc_no_check_bad() {
  int* p = (int*)malloc(sizeof(int));
  *p = 42;
  return p;
}

// BUG_TYPE: B-INTRA
// EXPECTED_STRATEGY: REPLACE
// CURRENT_STATUS: Works as expected
void bug_with_allocation_bad(int* x) {
  x = (int*)malloc(sizeof(int));
  int* y = NULL;
  *y = 42;
}

// BUG_TYPE: B-ALIAS
// EXPECTED_STRATEGY: REPLACE
// CURRENT_STATUS: Works as expected
void no_invalidation_compare_to_NULL_bad() {
  int x;
  int* p = &x; // Simulating unknown source
  int* q = &x;
  if (p == NULL) {
    q = p; // q becomes NULL
  }
  *q = 42;
}

/* ========================================================================= */
/* GROUP 2: THE "ADDRESS-OF" GUARD BUG (FIX-01)                              */
/* ========================================================================= */

// BUG_TYPE: B-ALIAS
// EXPECTED_STRATEGY: SKIP (Guard 'ptr')
// CURRENT_STATUS: INEFFECTIVE (Guards '&ptr' which is stack address)
// FIX_REQUIRED: FIX-01 (Smart Guard Selection)
void incr_deref(int* x, int* y) {
  (*x)++;
  (*y)++;
}

void call_incr_deref_with_alias_bad(void) {
  int x = 0;
  int* ptr = &x;
  incr_deref(ptr, ptr);
  if (x == 2) {
    ptr = NULL;
  }
  x = *ptr;
}

// BUG_TYPE: B-ARRAY
// EXPECTED_STRATEGY: SKIP (Guard 'vec' content or index)
// CURRENT_STATUS: INEFFECTIVE (Guards '&vec')
// FIX_REQUIRED: FIX-01
void nullptr_deref_young_bad(int* x) {
  int* vec[65] = {x, x, x, x, x, x, x, x, x, x, x, x, x, x,   x, x, x,
                  x, x, x, x, x, x, x, x, x, x, x, x, x, x,   x, x, x,
                  x, x, x, x, x, x, x, x, x, x, x, x, x, x,   x, x, x,
                  x, x, x, x, x, x, x, x, x, x, x, x, x, NULL};
  int p = *vec[64];
}

/* ========================================================================= */
/* GROUP 3: VIACALL LOCATION MAPPING (FIX-02)                                */
/* ========================================================================= */

// BUG_TYPE: B-FILE-API
// EXPECTED_STRATEGY: SKIP (Guard 'f' before getc)
// CURRENT_STATUS: NO_PLAN (Reason: "Already syntactically guarded")
// FIX_REQUIRED: FIX-02 (ViaCall Mapping)
void no_fopen_check_getc_bad() {
  FILE* f;
  int i;
  f = fopen("this_file_doesnt_exist", "r");
  i = getc(f); // Crash inside library
  printf("i =%i\n", i);
  fclose(f);
}

// BUG_TYPE: B-INTER
// EXPECTED_STRATEGY: SKIP (Guard 'joe' before call)
// CURRENT_STATUS: NO_PLAN
// FIX_REQUIRED: FIX-02
struct Person { int age; };
struct Person* Person_create(int age) { return NULL; }
int get_age(struct Person* who) { return who->age; }

int null_pointer_interproc_bad() {
  struct Person* joe = Person_create(32);
  return get_age(joe); // Crash inside get_age
}

/* ========================================================================= */
/* GROUP 4: SCOPING & COMPILATION ERRORS (FIX-03)                            */
/* ========================================================================= */

// BUG_TYPE: B-ARITH
// EXPECTED_STRATEGY: NONE (Cannot fix in caller) or EVADE (if possible)
// CURRENT_STATUS: INCORRECT (Guards 'p' which exists only in callee)
// FIX_REQUIRED: FIX-03 (Scope Check)
void assume_non_negative(int x) { if (x < 0) exit(1); }
void if_negative_then_crash_latent(int x) {
  assume_non_negative(-x);
  int* p = NULL;
  *p = 42;
}
void call_if_negative_then_crash_with_local_bad() {
  int x = rand();
  if_negative_then_crash_latent(x);
}

// BUG_TYPE: B-CYCLE
// EXPECTED_STRATEGY: NONE or SKIP (Caller side)
// CURRENT_STATUS: INCORRECT (Guards 'crash' which is in callee)
// FIX_REQUIRED: FIX-03
struct node { int data; struct node* next; };
void traverse_and_crash_if_equal_to_root(struct node* p) {
  struct node* old_p = p;
  while (p != NULL) {
    p = p->next;
    if (old_p == p) {
      int* crash = NULL;
      *crash = 42;
    }
  }
}
void crash_after_one_node_bad(struct node* q) {
  q->next = q;
  traverse_and_crash_if_equal_to_root(q);
}

/* ========================================================================= */
/* GROUP 5: RETURN SAFETY & EVADE (FIX-04, FIX-06)                           */
/* ========================================================================= */

// BUG_TYPE: B-STRUCT
// EXPECTED_STRATEGY: EVADE (Early return)
// CURRENT_STATUS: UNSAFE (Wraps return, falls through end of function)
// FIX_REQUIRED: FIX-04 (Return Safety) & FIX-06 (Evade Trigger)
int simple_null_pointer_bad() {
  struct Person* max = NULL;
  return max->age;
}

// BUG_TYPE: B-ANGELIC-SKIP
// EXPECTED_STRATEGY: EVADE
// CURRENT_STATUS: UNSAFE (Wraps return)
// FIX_REQUIRED: FIX-04 & FIX-06
struct delicious { int* ptr; };
extern void struct_ptr_skip(struct delicious* s){return;};
int struct_value_by_ref_ptr_write_bad() {
  struct delicious x;
  struct_ptr_skip(&x);
  x.ptr = NULL;
  return *x.ptr;
}

/* ========================================================================= */
/* GROUP 6: NEW CANDIDATES (STRUCTURAL COVERAGE)                             */
/* ========================================================================= */

// BUG_TYPE: B-FUNC-PTR
// EXPECTED_STRATEGY: REPLACE
// CURRENT_STATUS: OPTIMAL
static int* return_null() { return NULL; }
void null_pointer_with_function_pointer_bad() {
  int* (*fp)();
  fp = return_null;
  int* x = fp();
  *x = 3;
}

// BUG_TYPE: B-ALLOC-FAIL
// EXPECTED_STRATEGY: SKIP (Guard 'q')
// CURRENT_STATUS: OPTIMAL
void FPuseafterfree_no_check_for_null_after_realloc_bad() {
  int* p = (int*)malloc(sizeof(int) * 5);
  int* q = (int*)realloc(p, sizeof(int) * 10);
  if (!q)
    free(p);
  q[7] = 0; // Null Deref on failure
  free(q);
}

// BUG_TYPE: B-STACK-STRUCT
// EXPECTED_STRATEGY: SKIP (Guard 'l.next')
// CURRENT_STATUS: INEFFECTIVE (Guards '&l')
// FIX_REQUIRED: FIX-01 (Smart Guard Selection)
struct list { struct list* next; int data; };
void access_null_deref_bad() {
  struct list l = {NULL, 44};
  l.next->next = NULL;
}

// BUG_TYPE: B-FUNPTR-INDIRECT
// EXPECTED_STRATEGY: SKIP (Guard 'ptr')
// CURRENT_STATUS: INEFFECTIVE (Guards '&ptr')
// FIX_REQUIRED: FIX-01
void assign_NULL(int** ptr) { *ptr = NULL; }
void call_funptr(void (*funptr)(int**), int** ptr) { (*funptr)(ptr); }
void test_syntactic_specialization_bad(int* ptr) {
  call_funptr(&assign_NULL, &ptr);
  *ptr = 42;
}

// BUG_TYPE: B-SHORT-CIRCUIT
// EXPECTED_STRATEGY: SKIP (Guard 'p' deref)
// CURRENT_STATUS: FRAGMENTED (Two separate plans)
// FIX_REQUIRED: FIX-07 (Compaction Logic)
struct data { int flag; };
static struct data d;
int ternary2_bad(int x) {
  struct data* p = x ? &d : 0;
  return p->flag && p; // Deref before check
}

/* ========================================================================= */
/* CATEGORY: B-ARRAY-DECAY (Array to Pointer Decay)                          */
/* Aim: Inter-procedural trace where NULL is passed as array arg.      */
/*            Currently fails due to ViaCall Location Mapping (FIX-02).      */
/* ========================================================================= */

void set_ptr(int* ptr, int val) { *ptr = val; }

void set_ptr_param_array_get_null_bad() {
  set_ptr(NULL, 42); // Passing NULL where an array/pointer is expected
}

/* ========================================================================= */
/* CATEGORY: B-STRUCT-CALLBACK (Structs with Function Pointers)              */
/* Aim: "Address-of Bug" - Tracing through struct fields.              */
/* ========================================================================= */

typedef struct { void (*f)(int**); } callback_s;
void apply_callback(callback_s* cb, int** ptr) { (*cb->f)(ptr); }

void test_assign_NULL_callback_bad(int* ptr) {
  callback_s cb = {.f = &assign_NULL};
  apply_callback(&cb, &ptr);
  *ptr = 42; // Crash
}

/* ========================================================================= */
/* CATEGORY: B-MANIFEST (Latent Bug becoming Manifest)                       */
/* Aim: "Scoping Bug" - Tool tries to guard 'x' in 'main', but bug     */
/*            requires guarding logic inside 'latent_use'.                   */
/* ========================================================================= */

void latent_use(int* x) {
  *x = 42; // Crash if x is null
}

void main_manifest_bad() {
  int* x = NULL;
  latent_use(x);
}


struct Node {
    int val;
    struct Node* next;
    struct Node* prev;
};

struct Container {
    struct Node* inner;
};

/* ========================================================================= */
/* GROUP 1: CONTROL FLOW COMPLEXITY                                          */
/* Aim: Can the LCA algorithm find the correct scope in switches/loops?*/
/* ========================================================================= */

// VARIATION: Switch statement (Fallthrough)
// Expected: SKIP (Guard case 1) or REPLACE
void switch_npe_bad(int x) {
    int* p = &x;
    switch (x) {
        case 0:
            p = NULL;
            // fallthrough
        case 1:
            *p = 42; // Crash here if x was 0
            break;
    }
}

// VARIATION: Switch with Default
void switch_default_bad(int x) {
    int* p;
    switch (x) {
        case 1: p = &x; break;
        default: p = NULL; break;
    }
    *p = 10; // Crash if x != 1
}

// VARIATION: For-loop initialization
void for_loop_init_bad() {
    int* p = NULL;
    // Crash in the init/condition check logic
    for (int i = *p; i < 10; i++) {
        printf("%d", i);
    }
}

// VARIATION: For-loop body (conditional null)
void for_loop_body_bad() {
    int* p = malloc(sizeof(int));
    for (int i = 0; i < 5; i++) {
        if (i == 3) {
            free(p);
            p = NULL;
        }
        if (i == 4) {
            *p = 5; // Crash on last iteration
        }
    }
    if (p) free(p);
}

// VARIATION: While loop condition
void while_cond_bad() {
    int* p = NULL;
    while (*p != 0) { // Crash immediately
        p++;
    }
}

// VARIATION: Do-While loop
void do_while_bad() {
    int* p = NULL;
    do {
        *p = 1; // Crash
    } while (0);
}

// VARIATION: Goto (Unstructured Flow)
// Aim: Compaction logic often fails on jumps
void goto_npe_bad() {
    int* p = NULL;
    goto jump;

    p = malloc(sizeof(int)); // Skipped

jump:
    *p = 42; // Crash
    if (p) free(p);
}

/* ========================================================================= */
/* GROUP 2: ALIASING DEPTH & POINTER ARITHMETIC                              */
/* Aim: Can 'is_local' and alias analysis track deep chains?           */
/* ========================================================================= */

// VARIATION: Alias Chain Depth 3
void alias_chain_3_bad() {
    int* p = NULL;
    int* q = p;
    int* r = q;
    *r = 10; // Crash
}

// VARIATION: Double Pointer Indirection
void double_ptr_bad() {
    int* p = NULL;
    int** pp = &p;
    **pp = 5; // Crash
}

// VARIATION: Triple Pointer Indirection
void triple_ptr_bad() {
    int* p = NULL;
    int** pp = &p;
    int*** ppp = &pp;
    ***ppp = 5; // Crash
}

// VARIATION: Array Aliasing
void array_alias_bad() {
    int* arr[2];
    arr[0] = NULL;
    int* p = arr[0];
    *p = 1; // Crash
}

// VARIATION: Pointer Arithmetic (Invalid offset)
void ptr_arithmetic_bad() {
    int arr[2] = {0, 1};
    int* p = arr;
    p = NULL;
    // Pulse might track this as arithmetic on NULL
    *(p + 1) = 5; // Crash
}

/* ========================================================================= */
/* GROUP 3: NESTED STRUCTURES & FIELDS                                       */
/* Aim: Smart Guard Selection (Must guard p->next, not p)              */
/* ========================================================================= */

// VARIATION: Nested Struct Pointer (p->inner->val)
void nested_struct_ptr_bad() {
    struct Node inner = {0, NULL, NULL};
    struct Container c;
    c.inner = NULL;

    // Crash on dereferencing c.inner
    int x = c.inner->val;
}

// VARIATION: Triple Nesting (c->inner->next->val)
void triple_nested_bad() {
    struct Container* c = malloc(sizeof(struct Container));
    c->inner = malloc(sizeof(struct Node));
    c->inner->next = NULL;

    // Crash on c->inner->next
    c->inner->next->val = 5;

    free(c->inner);
    free(c);
}

// VARIATION: Stack Struct with Null Field (Requires Field Guard)
void stack_struct_null_field_bad() {
    struct Node n;
    n.next = NULL;

    // &n is valid, n.next is NULL
    int val = n.next->val; // Crash
}

// VARIATION: Struct Array Access
void struct_array_bad() {
    struct Node nodes[5];
    nodes[0].next = NULL;

    // Crash on nodes[0].next
    nodes[0].next->val = 10;
}

/* ========================================================================= */
/* GROUP 4: LIBRARY & STRING API MISUSE                                      */
/* Aim: ViaCall Location Mapping (FIX-02)                              */
/* ========================================================================= */

// VARIATION: strlen
void lib_strlen_bad() {
    char* s = NULL;
    int len = strlen(s); // Crash inside libc
}

// VARIATION: strcmp (First Arg)
void lib_strcmp_1_bad() {
    char* s = NULL;
    if (strcmp(s, "test") == 0) { } // Crash
}

// VARIATION: strcmp (Second Arg)
void lib_strcmp_2_bad() {
    char* s = NULL;
    if (strcmp("test", s) == 0) { } // Crash
}

// VARIATION: strdup
void lib_strdup_bad() {
    char* s = NULL;
    char* copy = strdup(s); // Crash
    if (copy) free(copy);
}

// VARIATION: memcpy (Source Null)
void lib_memcpy_src_bad() {
    char buf[10];
    char* src = NULL;
    memcpy(buf, src, 5); // Crash
}

// VARIATION: memcpy (Dest Null)
void lib_memcpy_dest_bad() {
    char* dest = NULL;
    memcpy(dest, "test", 4); // Crash
}

/* ========================================================================= */
/* GROUP 5: RETURN SAFETY & VOID CONTEXT                                     */
/* Aim: Safety Check (FIX-04) - Must avoid UB                        */
/* ========================================================================= */

// VARIATION: Void function return (Skip is Safe)
void void_return_bad() {
    int* p = NULL;
    *p = 10; // Crash
    return;
}

// VARIATION: Int function return (Skip is Unsafe -> Needs Evade)
int int_return_bad() {
    int* p = NULL;
    *p = 10; // Crash
    return *p;
}

// VARIATION: Pointer return (Skip is Unsafe -> Needs Evade)
int* ptr_return_bad() {
    int* p = NULL;
    *p = 10; // Crash
    return p;
}

// VARIATION: Return in middle of block
int mid_block_return_bad(int x) {
    int* p = NULL;
    if (x > 5) {
        *p = 10; // Crash
        return 1;
    }
    return 0;
}

/* ========================================================================= */
/* GROUP 6: CALLER / CALLEE SCOPING                                          */
/* Aim: Scoping Check (FIX-03) - Ensure visibility                   */
/* ========================================================================= */

void helper_deref(int* p) {
    *p = 10; // Crash
}

// VARIATION: Pass Null Literal (Caller)
void call_literal_bad() {
    helper_deref(NULL);
}

// VARIATION: Pass Local Null (Caller)
void call_local_bad() {
    int* p = NULL;
    helper_deref(p);
}

// VARIATION: Pass Global Null (Caller)
int* g_ptr = NULL;
void call_global_bad() {
    helper_deref(g_ptr);
}

// VARIATION: Deep Call Chain
void helper_2(int* p) { *p = 5; }
void helper_1(int* p) { helper_2(p); }
void call_deep_bad() {
    helper_1(NULL);
}

/* ========================================================================= */
/* GROUP 7: TYPE CONFUSION (ARITHMETIC & CASTS)                              */
/* Aim: PulseX Analysis & Guard Logic                                  */
/* ========================================================================= */

// VARIATION: Void* Cast
void void_cast_bad() {
    void* p = NULL;
    int* i = (int*)p;
    *i = 10; // Crash
}

// VARIATION: Long to Ptr cast
void long_cast_bad() {
    long l = 0;
    int* p = (int*)l;
    *p = 10; // Crash
}

// VARIATION: Boolean Logic (De Morgan's)
void boolean_logic_bad(int x, int y) {
    int* p = NULL;
    // (x || y) being true doesn't save p
    if (x || y) {
        *p = 10; // Crash
    }
}

// VARIATION: Ternary Assignment
void ternary_assign_bad(int flag) {
    int x = 5;
    int* p = flag ? &x : NULL;
    *p = 10; // Crash if flag is false
}

// VARIATION: Short-circuit Assignment
void short_circuit_assign_bad(int* input) {
    // If input is null, p becomes null
    int* p = input;
    if (p && *p > 0) {
        // Safe
    }

    // Reset p
    p = NULL;

    // Bad check
    int val = (p != NULL) || *p; // Crash on RHS
}

// VARIATION: Comma Operator
void comma_op_bad() {
    int* p = NULL;
    int x = (p = NULL, *p); // Crash
}

/*
=======================================================================
   GROUP 8: EVADE STRATEGY TRIGGERS (Parameters & Return Safety)
   Note: We force manifest errors inside the function by checking for NULL
   and then dereferencing it. This makes the bug local to the function
   while keeping the variable as a parameter.
=======================================================================
*/

// VARIATION 1: Bad Error Handling (Dereference in error path)
// Expectation: EVADE.
// Logic: "If p is null, crash". This is a manifest bug in this function.
// Since 'p' is a parameter, Evade (Early Return) is the valid fix.
int test_evade_simple(int* p) {
    if (p == NULL) {
        return *p; // Crash
    }
    return 0;
}

// VARIATION 2: Arithmetic on Parameter with Bad Check
// Expectation: EVADE.
int test_evade_arith(int* arr) {
    if (arr == NULL) {
        return arr[5]; // Crash
    }
    return 0;
}

// VARIATION 3: Void Function Bad Logic
// Expectation: EVADE.
void test_evade_void(int* p) {
    if (p == NULL) {
        *p = 10; // Crash
        return;
    }
    *p = 20;
}

int main() {
    return 0;
}
