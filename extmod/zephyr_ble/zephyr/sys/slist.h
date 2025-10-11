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

// Get next node
static inline sys_snode_t *sys_slist_peek_next(sys_snode_t *node) {
    return node ? node->next : NULL;
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

// Insert node after prev_node
static inline void sys_slist_insert(sys_slist_t *list, sys_snode_t *prev_node, sys_snode_t *node) {
    if (prev_node == NULL) {
        // Insert at head
        sys_slist_prepend(list, node);
    } else {
        node->next = prev_node->next;
        prev_node->next = node;

        // Update tail if inserting at end
        if (node->next == NULL) {
            list->tail = node;
        }
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

// Get from list (non-empty version, asserts if empty)
static inline sys_snode_t *sys_slist_get_not_empty(sys_slist_t *list) {
    sys_snode_t *node = sys_slist_get(list);
    // In full Zephyr this would assert if node is NULL
    return node;
}

// Find node in list
static inline sys_snode_t *sys_slist_find(sys_slist_t *list, sys_snode_t *node, sys_snode_t **prev) {
    sys_snode_t *curr = list->head;
    sys_snode_t *p = NULL;

    while (curr != NULL) {
        if (curr == node) {
            if (prev) {
                *prev = p;
            }
            return curr;
        }
        p = curr;
        curr = curr->next;
    }

    return NULL;
}

// Remove node from list (returns true if found and removed)
static inline bool sys_slist_remove(sys_slist_t *list, sys_snode_t *prev_node, sys_snode_t *node) {
    if (prev_node == NULL) {
        // Removing head
        if (list->head == node) {
            list->head = node->next;
            if (list->tail == node) {
                list->tail = NULL;
            }
            return true;
        }
    } else {
        // Removing after prev_node
        if (prev_node->next == node) {
            prev_node->next = node->next;
            if (list->tail == node) {
                list->tail = prev_node;
            }
            return true;
        }
    }
    return false;
}

// Iteration macros
#define SYS_SLIST_FOR_EACH_NODE(list, node) \
    for (node = sys_slist_peek_head(list); node != NULL; node = node->next)

#define SYS_SLIST_FOR_EACH_NODE_SAFE(list, node, next) \
    for ((node) = sys_slist_peek_head(list), \
         (next) = sys_slist_peek_next(node); \
         (node) != NULL; \
         (node) = (next), (next) = sys_slist_peek_next(node))

// Container-of for getting struct from node
#define SYS_SLIST_CONTAINER(node, type, member) \
    ((type *)(((char *)(node)) - offsetof(type, member)))

// Static list initializer
#define SYS_SLIST_STATIC_INIT(ptr_to_list) {NULL, NULL}

// Peek head container (get container struct from list head node)
#define SYS_SLIST_PEEK_HEAD_CONTAINER(list, container_ptr, member) \
    ((sys_slist_peek_head(list) != NULL) ? \
     SYS_SLIST_CONTAINER(sys_slist_peek_head(list), __typeof__(*(container_ptr)), member) : \
     NULL)

// Peek tail container (get container struct from list tail node)
#define SYS_SLIST_PEEK_TAIL_CONTAINER(list, container_ptr, member) \
    ((sys_slist_peek_tail(list) != NULL) ? \
     SYS_SLIST_CONTAINER(sys_slist_peek_tail(list), __typeof__(*(container_ptr)), member) : \
     NULL)

// Peek next container (get next container struct from current container)
#define SYS_SLIST_PEEK_NEXT_CONTAINER(container_ptr, member) \
    (((container_ptr) != NULL && (container_ptr)->member.next != NULL) ? \
     SYS_SLIST_CONTAINER((container_ptr)->member.next, __typeof__(*(container_ptr)), member) : \
     NULL)

#define SYS_SLIST_FOR_EACH_CONTAINER(list, node, member) \
    for (node = SYS_SLIST_CONTAINER(sys_slist_peek_head(list), __typeof__(*node), member); \
         &node->member != NULL; \
         node = SYS_SLIST_CONTAINER(node->member.next, __typeof__(*node), member))

#define SYS_SLIST_FOR_EACH_CONTAINER_SAFE(list, node, next_var, member) \
    for ((node) = SYS_SLIST_PEEK_HEAD_CONTAINER(list, node, member), \
         (next_var) = SYS_SLIST_PEEK_NEXT_CONTAINER(node, member); \
         (node) != NULL; \
         (node) = (next_var), \
         (next_var) = SYS_SLIST_PEEK_NEXT_CONTAINER(node, member))

#endif /* ZEPHYR_SYS_SLIST_H_ */
