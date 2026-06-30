// Test 13: Null function pointer call
#include <stdio.h>

typedef void (*callback_t)(int);

void process(callback_t cb, int value) {
    // Bug: not checking if cb is NULL
    cb(value);
}

int main() {
    callback_t handler = NULL;
    
    process(handler, 42);  // Will crash
    
    return 0;
}