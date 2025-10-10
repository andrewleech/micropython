/*
 * Zephyr sys/slist.h wrapper for MicroPython
 * Single-linked list implementation
 */

#ifndef ZEPHYR_SYS_SLIST_H_
#define ZEPHYR_SYS_SLIST_H_

#include <stddef.h>
#include <stdbool.h>

// Single-linked list node
struct _snode {
    struct _snode *next;
};

typedef struct _snode sys_snode_t;

// Single-linked list
struct _slist {
    sys_snode_t *head;
    sys_snode_t *tail;
};

typedef struct _slist sys_slist_t;

// Initialize list
static inline void sys_slist_init(sys_slist_t *list) {
    list->head = NULL;
    list->tail = NULL;
}

// Check if list is empty
static inline bool sys_slist_is_empty(sys_slist_t *list) {
    return list->head == NULL;
}

// Get head node
static inline sys_snode_t *sys_slist_peek_head(sys_slist_t *list) {
    return list->head;
}

// Get tail node
static inline sys_snode_t *sys_slist_peek_tail(sys_slist_t *list) {
    return list->tail;
}

// Append node to tail
static inline void sys_slist_append(sys_slist_t *list, sys_snode_t *node) {
    node->next = NULL;
    
    if (list->tail == NULL) {
        list->head = node;
        list->tail = node;
    } else {
        list->tail->next = node;
        list->tail = node;
    }
}

// Prepend node to head
static inline void sys_slist_prepend(sys_slist_t *list, sys_snode_t *node) {
    node->next = list->head;
    list->head = node;
    
    if (list->tail == NULL) {
        list->tail = node;
    }
}

// Remove head node
static inline sys_snode_t *sys_slist_get(sys_slist_t *list) {
    sys_snode_t *node = list->head;
    
    if (node != NULL) {
        list->head = node->next;
        if (list->head == NULL) {
            list->tail = NULL;
        }
    }
    
    return node;
}

// Find and remove node
static inline bool sys_slist_find_and_remove(sys_slist_t *list, sys_snode_t *node) {
    sys_snode_t *prev = NULL;
    sys_snode_t *curr = list->head;
    
    while (curr != NULL) {
        if (curr == node) {
            if (prev == NULL) {
                list->head = curr->next;
            } else {
                prev->next = curr->next;
            }
            
            if (list->tail == curr) {
                list->tail = prev;
            }
            
            return true;
        }
        prev = curr;
        curr = curr->next;
    }
    
    return false;
}

// Iteration macros
#define SYS_SLIST_FOR_EACH_NODE(list, node) \
    for (node = sys_slist_peek_head(list); node != NULL; node = node->next)

#define SYS_SLIST_FOR_EACH_NODE_SAFE(list, node, next) \
    for (node = sys_slist_peek_head(list); \
         node != NULL && (next = node->next, 1); \
         node = next)

// Container-of for getting struct from node
#define SYS_SLIST_CONTAINER(node, type, member) \
    ((type *)(((char *)(node)) - offsetof(type, member)))

#define SYS_SLIST_FOR_EACH_CONTAINER(list, node, member) \
    for (node = SYS_SLIST_CONTAINER(sys_slist_peek_head(list), __typeof__(*node), member); \
         &node->member != NULL; \
         node = SYS_SLIST_CONTAINER(node->member.next, __typeof__(*node), member))

#endif /* ZEPHYR_SYS_SLIST_H_ */
