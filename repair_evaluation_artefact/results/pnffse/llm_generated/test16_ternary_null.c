// Test 16: Ternary operator null path
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char *argv[]) {
    // Ternary can return NULL
    int *value = (argc > 1) ? malloc(sizeof(int)) : NULL;
    
    // Bug: not checking which path was taken
    *value = 42;
    
    printf("Value: %d\n", *value);
    free(value);
    return 0;
}