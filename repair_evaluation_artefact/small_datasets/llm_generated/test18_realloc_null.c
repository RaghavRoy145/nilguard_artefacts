// Test 18: Realloc failure not checked
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main() {
    int *data = malloc(10 * sizeof(int));
    
    // realloc can return NULL on failure
    data = realloc(data, SIZE_MAX);
    
    // Bug: not checking if realloc failed
    data[0] = 1;
    data[1] = 2;
    
    printf("Data: %d, %d\n", data[0], data[1]);
    free(data);
    return 0;
}
