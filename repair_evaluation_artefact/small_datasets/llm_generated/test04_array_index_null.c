// Test 04: Null array indexing
#include <stdio.h>

int main() {
    int *array = NULL;
    
    // Bug: indexing into NULL array
    array[0] = 1;
    array[1] = 2;
    array[2] = 3;
    
    printf("First element: %d\n", array[0]);
    return 0;
}