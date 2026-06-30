// Test 09: Linked list null traversal
#include <stdio.h>
#include <stdlib.h>

typedef struct Node {
    int data;
    struct Node *next;
} Node;

int main() {
    Node *head = NULL;
    
    // Bug: accessing NULL head
    head->data = 10;
    
    // Bug: traversing from NULL
    Node *current = head;
    while (current != NULL) {
        printf("Data: %d\n", current->data);
        current = current->next;
    }
    
    return 0;
}