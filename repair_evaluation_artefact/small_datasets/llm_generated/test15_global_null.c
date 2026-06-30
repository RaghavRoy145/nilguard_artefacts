// Test 15: Global pointer null dereference
#include <stdio.h>

int *global_ptr = NULL;

void use_global() {
    // Bug: global_ptr is NULL
    *global_ptr = 999;
}

int main() {
    use_global();
    printf("Global value: %d\n", *global_ptr);
    return 0;
}