// Test 10: Null dereference in switch case
#include <stdio.h>

int main(int argc, char *argv[]) {
    int *ptr = NULL;
    
    switch (argc) {
        case 1:
            ptr = NULL;
            break;
        case 2:
            ptr = NULL;
            *ptr = 20;
            break;
        default:
            ptr = NULL;
            *ptr = 30;
            break;
    }
    
    // Bug: ptr might be NULL from case 1
    printf("Value: %d\n", *ptr);
    
    free(ptr);
    return 0;
}
