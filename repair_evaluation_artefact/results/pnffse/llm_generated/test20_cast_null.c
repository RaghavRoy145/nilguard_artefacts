// Test 20: Type cast null dereference
#include <stdio.h>

typedef struct {
    int type;
    void *data;
} Container;

int main() {
    Container c = {1, NULL};
    
    // Bug: casting and dereferencing NULL
    int *int_data = (int *)c.data;
    *int_data = 100;
    
    // Another cast dereference
    char *char_data = (char *)c.data;
    *char_data = 'X';
    
    printf("Int: %d, Char: %c\n", *int_data, *char_data);
    return 0;
}