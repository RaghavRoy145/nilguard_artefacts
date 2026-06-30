// Test 11: Arithmetic operation on null pointer
#include <stdio.h>

int main() {
    int *numbers = NULL;
    int sum = 0;
    
    // Bug: arithmetic with NULL pointer
    for (int i = 0; i < 3; i++) {
        sum += *(numbers + i);  // NULL + offset still NULL
    }
    
    printf("Sum: %d\n", sum);
    return 0;
}