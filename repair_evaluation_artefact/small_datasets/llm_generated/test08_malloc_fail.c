// Test 08: Failed malloc not checked
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main() {
    // Simulate malloc failure by requesting huge amount
    int *buffer = malloc(SIZE_MAX);
    
    // Bug: not checking if malloc returned NULL
    buffer[0] = 123;
    buffer[1] = 456;
    
    printf("Buffer: %d, %d\n", buffer[0], buffer[1]);
    free(buffer);
    return 0;
}
