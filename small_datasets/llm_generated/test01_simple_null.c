// Test 01: Simple direct null dereference
#include <stdio.h>

int main() {
    int *ptr = NULL;
    *ptr = 42;  // Direct null dereference
    printf("Value: %d\n", *ptr);
    return 0;
}