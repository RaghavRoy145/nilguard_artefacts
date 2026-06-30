// Test 14: Nested struct null access
#include <stdio.h>

typedef struct {
    int id;
    char *name;
} Person;

typedef struct {
    Person *owner;
    int balance;
} Account;

int main() {
    Account acc = {NULL, 1000};
    
    // Bug: accessing NULL owner
    acc.owner->id = 123;
    
    printf("Owner ID: %d\n", acc.owner->id);
    return 0;
}