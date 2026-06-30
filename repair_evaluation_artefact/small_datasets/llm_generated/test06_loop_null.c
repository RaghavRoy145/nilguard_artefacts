// Test 06: Null dereference in loop
#include <stdio.h>

int main() {
    int *values = NULL;
    
    // Bug: dereferencing NULL in loop
    for (int i = 0; i < 5; i++) {
        values[i] = i * 10;
    }
    
    for (int i = 0; i < 5; i++) {
        printf("values[%d] = %d\n", i, values[i]);
    }
    
    return 0;
}