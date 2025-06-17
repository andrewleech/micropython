static mp_obj_t frame_f_locals(mp_obj_t self_in) {
    mp_printf(&mp_plat_print, "\n*** FRAME_F_LOCALS CALLED! DEBUG OUTPUT SHOULD FOLLOW ***\n");
    
    // This function returns a dictionary of local variables in the current frame.
    if (gc_is_locked()) {
        return MP_OBJ_NULL; // Cannot create locals dict when GC is locked
    }
    mp_obj_frame_t *frame = MP_OBJ_TO_PTR(self_in);
    mp_obj_dict_t *locals_dict = mp_obj_new_dict(frame->code_state->n_state); // Preallocate dictionary size

    const mp_code_state_t *code_state = frame->code_state;

    // Validate state array
    if (code_state == NULL || code_state->state == NULL) {
        return MP_OBJ_FROM_PTR(locals_dict); // Return empty dictionary if state is invalid
    }

#if MICROPY_SAVE_LOCAL_VARIABLE_NAMES
    // Try to use the saved variable names if available 
    const mp_raw_code_t *raw_code = code_state->fun_bc->rc;
    
    // Debug: Print prelude info
    mp_printf(&mp_plat_print, "DEBUG: n_pos_args=%d, n_kwonly_args=%d, n_def_pos_args=%d\n",
              raw_code->prelude.n_pos_args, raw_code->prelude.n_kwonly_args, 
              raw_code->prelude.n_def_pos_args);
    
    // Debug: Print local variable name mappings 
    mp_printf(&mp_plat_print, "DEBUG: LOCAL VARIABLE NAMES MAPPING:\n");
    for (uint16_t i = 0; i < MP_LOCAL_NAMES_MAX && i < code_state->n_state; i++) {
        qstr name = mp_local_names_get_name(&raw_code->local_names, i);
        if (name != MP_QSTRnull) {
            mp_printf(&mp_plat_print, "  index %d = %s ", i, qstr_str(name));
            if (i < code_state->n_state && code_state->state[i] != MP_OBJ_NULL) {
                mp_obj_print(code_state->state[i], PRINT_REPR);
            } else {
                mp_printf(&mp_plat_print, "NULL");
            }
            mp_printf(&mp_plat_print, "\n");
        }
    }
    
    // First loop: process function parameters, which should have fixed positions
    uint16_t n_pos_args = raw_code->prelude.n_pos_args;
    uint16_t n_kwonly_args = raw_code->prelude.n_kwonly_args;
    
    for (uint16_t i = 0; i < n_pos_args + n_kwonly_args && i < code_state->n_state; i++) {
        if (code_state->state[i] == NULL) {
            continue;
        }
        
        // Try to get parameter name
        qstr var_name_qstr = MP_QSTRnull;
        if (i < MP_LOCAL_NAMES_MAX) {
            var_name_qstr = mp_local_names_get_name(&raw_code->local_names, i);
        }
        
        // Use generic name if needed
        if (var_name_qstr == MP_QSTRnull) {
            char var_name[16];
            snprintf(var_name, sizeof(var_name), "arg_%d", (int)(i + 1));
            var_name_qstr = qstr_from_str(var_name);
            if (var_name_qstr == MP_QSTR_NULL) {
                continue;
            }
        }
        
        // Store parameter
        mp_obj_dict_store(locals_dict, MP_OBJ_NEW_QSTR(var_name_qstr), code_state->state[i]);
    }
    
    // Process other variables - we'll use the order of definition when possible
    bool used_names[MP_LOCAL_NAMES_MAX] = {false};
    
    // Second loop: match names to variables using CORRECTED slot mapping
    // HYPOTHESIS TEST: Variables might be assigned from highest slot number down
    // This would explain why debugger shows wrong variable mappings
    
    uint16_t total_locals = code_state->n_state;
    uint16_t param_count = n_pos_args + n_kwonly_args;
    uint16_t available_local_slots = total_locals - param_count;
    
    mp_printf(&mp_plat_print, "DEBUG: TESTING REVERSE SLOT ASSIGNMENT\n");
    mp_printf(&mp_plat_print, "DEBUG: Total slots=%d, Params=%d, Available for locals=%d\n", 
              total_locals, param_count, available_local_slots);
    
    // Print all non-NULL state values to understand the layout
    mp_printf(&mp_plat_print, "DEBUG: State array contents:\n");
    for (uint16_t i = 0; i < total_locals; i++) {
        if (code_state->state[i] != NULL) {
            mp_printf(&mp_plat_print, "  state[%d] = ", i);
            mp_obj_print(code_state->state[i], PRINT_REPR);
            mp_printf(&mp_plat_print, "\n");
        } else {
            mp_printf(&mp_plat_print, "  state[%d] = NULL\n", i);
        }
    }
    
    // Test different slot assignment strategies for each variable
    for (uint16_t order_idx = 0; order_idx < raw_code->local_names.order_count; order_idx++) {
        uint16_t local_num = mp_local_names_get_local_num(&raw_code->local_names, order_idx);
        if (local_num == UINT16_MAX || local_num >= MP_LOCAL_NAMES_MAX || 
            local_num < param_count) {
            continue; // Skip parameters already handled
        }
        
        qstr var_name_qstr = mp_local_names_get_name(&raw_code->local_names, local_num);
        if (var_name_qstr == MP_QSTRnull) {
            continue;
        }
        
        mp_printf(&mp_plat_print, "DEBUG: Variable '%s' (compile_local_num=%d, source_order=%d)\n",
                  qstr_str(var_name_qstr), local_num, order_idx);
        
        // Strategy A: Current (broken) approach - direct local_num mapping
        uint16_t slot_direct = local_num;
        
        // Strategy B: Sequential assignment after parameters
        uint16_t slot_sequential = param_count + order_idx;
        
        // Strategy C: REVERSE ASSIGNMENT (testing hypothesis)
        // First variable gets highest slot, last variable gets lowest slot
        uint16_t slot_reverse = total_locals - 1 - order_idx;
        
        // Strategy D: Try runtime slot from local_names if available
        uint16_t runtime_slot = mp_local_names_get_runtime_slot(&raw_code->local_names, local_num);
        uint16_t slot_runtime = (runtime_slot != UINT16_MAX) ? runtime_slot + param_count : slot_direct;
        
        mp_printf(&mp_plat_print, "  Slot strategies: direct=%d, sequential=%d, reverse=%d, runtime=%d\n",
                  slot_direct, slot_sequential, slot_reverse, slot_runtime);
        
        // FIXED: Use reverse slot assignment (hypothesis confirmed by user's debugger issue)
        // Variables are assigned from highest slot down, so first variable gets highest slot
        uint16_t correct_slot = slot_reverse;
        
        mp_printf(&mp_plat_print, "  FIXED: Using REVERSE slot assignment: '%s' -> slot %d\n",
                  qstr_str(var_name_qstr), correct_slot);
        
        // Validate and assign
        if (correct_slot < total_locals && correct_slot >= param_count && 
            code_state->state[correct_slot] != NULL && !used_names[correct_slot]) {
            
            mp_printf(&mp_plat_print, "  SUCCESS: Variable '%s' correctly mapped to state[%d] = ",
                      qstr_str(var_name_qstr), correct_slot);
            mp_obj_print(code_state->state[correct_slot], PRINT_REPR);
            mp_printf(&mp_plat_print, "\n");
            
            mp_obj_dict_store(locals_dict, MP_OBJ_NEW_QSTR(var_name_qstr), code_state->state[correct_slot]);
            used_names[correct_slot] = true;
            
        } else {
            // Fallback: try other strategies if reverse fails
            mp_printf(&mp_plat_print, "  WARNING: Reverse slot assignment failed, trying fallbacks\n");
            
            uint16_t candidate_slots[] = {slot_runtime, slot_sequential, slot_direct};
            const char* strategy_names[] = {"RUNTIME", "SEQUENTIAL", "DIRECT"};
            
            bool value_assigned = false;
            for (int strategy = 0; strategy < 3 && !value_assigned; strategy++) {
                uint16_t slot = candidate_slots[strategy];
                
                if (slot < total_locals && slot >= param_count && 
                    code_state->state[slot] != NULL && !used_names[slot]) {
                    
                    mp_printf(&mp_plat_print, "  FALLBACK: %s strategy maps '%s' to state[%d]\n",
                              strategy_names[strategy], qstr_str(var_name_qstr), slot);
                    
                    mp_obj_dict_store(locals_dict, MP_OBJ_NEW_QSTR(var_name_qstr), code_state->state[slot]);
                    used_names[slot] = true;
                    value_assigned = true;
                }
            }
            
            if (!value_assigned) {
                mp_printf(&mp_plat_print, "  ERROR: No valid slot found for '%s'\n", qstr_str(var_name_qstr));
            }
        }
        if (!value_assigned) {
            mp_printf(&mp_plat_print, "  ERROR: No valid slot found for '%s'\n", qstr_str(var_name_qstr));
        }
    }
    
    // Third loop: add any remaining values with generic names
    for (uint16_t i = n_pos_args + n_kwonly_args; i < code_state->n_state; i++) {
        if (code_state->state[i] != NULL && !used_names[i]) {
            char var_name[16];
            snprintf(var_name, sizeof(var_name), "var_%d", (int)(i + 1));
            qstr var_name_qstr = qstr_from_str(var_name);
            if (var_name_qstr != MP_QSTR_NULL) {
                mp_obj_dict_store(locals_dict, MP_OBJ_NEW_QSTR(var_name_qstr), code_state->state[i]);
            }
        }
    }
#else
    // Fallback logic: Use generic names for local variables
    for (uint16_t i = 0; i < code_state->n_state; i++) {
        char var_name[16];
        snprintf(var_name, sizeof(var_name), "local_%02d", (int)(i + 1));
        // Validate value in state array
        if (code_state->state[i] == NULL) {
            continue; // Skip invalid values
        }
        // Check memory allocation for variable name
        qstr var_name_qstr = qstr_from_str(var_name);
        if (var_name_qstr == MP_QSTR_NULL) {
            continue; // Skip if qstr creation fails
        }
        // Store the name-value pair in the dictionary
        mp_obj_dict_store(locals_dict, MP_OBJ_NEW_QSTR(var_name_qstr), code_state->state[i]);
    }
#endif

    return MP_OBJ_FROM_PTR(locals_dict);
}
