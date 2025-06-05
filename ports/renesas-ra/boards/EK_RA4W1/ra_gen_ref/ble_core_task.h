/* generated thread header file - do not edit */
#ifndef BLE_CORE_TASK_H_
#define BLE_CORE_TASK_H_
#include "bsp_api.h"
                #include "FreeRTOS.h"
                #include "task.h"
                #include "semphr.h"
                #include "hal_data.h"
                #ifdef __cplusplus
                extern "C" void ble_core_task_entry(void * pvParameters);
                #else
                extern void ble_core_task_entry(void * pvParameters);
                #endif
#include "r_agt.h"
#include "r_timer_api.h"
#include "r_gpt.h"
#include "r_timer_api.h"
#include "r_flash_lp.h"
#include "r_flash_api.h"

#include "rm_ble_abs.h"
#include "rm_ble_abs_api.h"
FSP_HEADER
/** AGT Timer Instance */
extern const timer_instance_t g_timer1;

/** Access the AGT instance using these structures when calling API functions directly (::p_api is not used). */
extern agt_instance_ctrl_t g_timer1_ctrl;
extern const timer_cfg_t g_timer1_cfg;

#ifndef ble_abs_hw_timer_callback
void ble_abs_hw_timer_callback(timer_callback_args_t * p_args);
#endif
/** Timer on GPT Instance. */
extern const timer_instance_t g_timer0;

/** Access the GPT instance using these structures when calling API functions directly (::p_api is not used). */
extern gpt_instance_ctrl_t g_timer0_ctrl;
extern const timer_cfg_t g_timer0_cfg;

#ifndef r_rf_host_timer_interrupt
void r_rf_host_timer_interrupt(timer_callback_args_t * p_args);
#endif
/* Flash on Flash LP Instance. */
extern const flash_instance_t g_flash0;

/** Access the Flash LP instance using these structures when calling API functions directly (::p_api is not used). */
extern flash_lp_instance_ctrl_t g_flash0_ctrl;
extern const flash_cfg_t g_flash0_cfg;

#ifndef NULL
void NULL(flash_callback_args_t * p_args);
#endif

void r_ble_rf_notify_event_start(uint32_t param);
void r_ble_rf_notify_event_close(uint32_t param);
void r_ble_rf_notify_deep_sleep(uint32_t param);

/** BLE_ABS on BLE Instance. */
extern const ble_abs_instance_t g_ble_abs0;

/** Access the BLE_ABS instance using these structures when calling API functions directly (::p_api is not used). */
extern ble_abs_instance_ctrl_t g_ble_abs0_ctrl;
extern const ble_abs_cfg_t g_ble_abs0_cfg;

/** Callback used by ble_abs Instance. */
#ifndef gap_cb
void gap_cb(uint16_t type, ble_status_t result, st_ble_evt_data_t *p_data);
#endif

#ifndef vs_cb
void vs_cb(uint16_t type, ble_status_t result, st_ble_vs_evt_data_t *p_data);
#endif

#ifndef gs_abs_gatts_cb_param
extern ble_abs_gatt_server_callback_set_t gs_abs_gatts_cb_param[];
#else
ble_abs_gatt_server_callback_set_t gs_abs_gatts_cb_param[];
#endif

#ifndef gs_abs_gattc_cb_param
extern ble_abs_gatt_client_callback_set_t gs_abs_gattc_cb_param[];
#else
ble_abs_gatt_client_callback_set_t gs_abs_gattc_cb_param[];
#endif

#ifndef NULL
void NULL(ble_abs_callback_args_t * p_args);
#endif
FSP_FOOTER
#endif /* BLE_CORE_TASK_H_ */
