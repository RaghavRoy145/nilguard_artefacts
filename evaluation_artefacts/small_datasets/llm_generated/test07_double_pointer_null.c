// Test 07: Double pointer null dereference
#include <stdio.h>

int main() {
    int **ptr = NULL;
    int value = 42;
    
    // Bug: dereferencing NULL double pointer
    *ptr = &value;
    
    printf("Value through double pointer: %d\n", **ptr);
    return 0;
}