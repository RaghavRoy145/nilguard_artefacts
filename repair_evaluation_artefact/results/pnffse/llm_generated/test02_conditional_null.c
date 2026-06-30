// Test 02: Conditional null dereference
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char *argv[]) {
    int *data = NULL;
    
    if (argc > 1) {
        data = malloc(sizeof(int));
        *data = 100;
    }
    
    // Bug: data might be NULL if argc <= 1
    printf("Data value: %d\n", *data);
    
    free(data);
    return 0;
}