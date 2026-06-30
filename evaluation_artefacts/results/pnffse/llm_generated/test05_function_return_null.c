// Test 05: Function returning NULL
#include <stdio.h>
#include <stdlib.h>

int* create_array(int size) {
    if (size <= 0) {
        return NULL;  // Returns NULL for invalid size
    }
    return malloc(size * sizeof(int));
}

int main() {
    int *arr = create_array(0);  // Will return NULL
    
    // Bug: dereferencing NULL return value
    arr[0] = 100;
    
    printf("First value: %d\n", arr[0]);
    return 0;
}