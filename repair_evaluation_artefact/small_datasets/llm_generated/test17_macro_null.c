// Test 17: Macro hiding null dereference
#include <stdio.h>

#define GET_VALUE(ptr) (*(ptr))
#define SET_VALUE(ptr, val) (*(ptr) = (val))

int main() {
    int *ptr = NULL;
    
    // Bug: macro hides the dereference
    SET_VALUE(ptr, 100);
    
    int val = GET_VALUE(ptr);
    printf("Value: %d\n", val);
    
    return 0;
}