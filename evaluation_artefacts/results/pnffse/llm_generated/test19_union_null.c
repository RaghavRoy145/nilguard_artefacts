// Test 19: Union member null access
#include <stdio.h>

typedef union {
    int *int_ptr;
    char *char_ptr;
    float *float_ptr;
} MultiPtr;

int main() {
    MultiPtr ptr;
    ptr.int_ptr = NULL;
    
    // Bug: accessing NULL union member
    *ptr.int_ptr = 42;
    
    // All union members point to same NULL
    *ptr.char_ptr = 'A';
    *ptr.float_ptr = 3.14f;
    
    return 0;
}