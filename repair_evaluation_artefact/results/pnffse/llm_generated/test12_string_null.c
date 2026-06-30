// Test 12: String operations on NULL
#include <stdio.h>
#include <string.h>

int main() {
    char *str = NULL;
    
    // Bug: dereferencing NULL string
    if (*str == 'H') {
        printf("String starts with H\n");
    }
    
    // Bug: accessing NULL string character
    char first = str[0];
    printf("First character: %c\n", first);
    
    return 0;
}