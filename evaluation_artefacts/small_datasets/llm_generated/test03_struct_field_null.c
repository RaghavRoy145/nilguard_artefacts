// Test 03: Null struct pointer field access
#include <stdio.h>

typedef struct {
    int x;
    int y;
} Point;

int main() {
    Point *p = NULL;
    
    // Bug: accessing field of NULL struct
    p->x = 10;
    p->y = 20;
    
    printf("Point: (%d, %d)\n", p->x, p->y);
    return 0;
}