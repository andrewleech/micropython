/* generated thread source file - do not edit */
#include "ble_core_task.h"


#if 1
                static StaticTask_t ble_core_task_memory;
                #if defined(__ARMCC_VERSION)           /* AC6 compiler */
                static uint8_t ble_core_task_stack[2048] BSP_PLACE_IN_SECTION(BSP_UNINIT_SECTION_PREFIX ".stack.thread") BSP_ALIGN_VARIABLE(BSP_STACK_ALIGNMENT);
                #else
                static uint8_t ble_core_task_stack[2048] BSP_PLACE_IN_SECTION(BSP_UNINIT_SECTION_PREFIX ".stack.ble_core_task") BSP_ALIGN_VARIABLE(BSP_STACK_ALIGNMENT);
                #endif
                #endif
                TaskHandle_t ble_core_task;
                void ble_core_task_create(void);
                static void ble_core_task_func(void * pvParameters);
                void rtos_startup_err_callback(void * p_instance, void * p_data);
                void rtos_startup_common_init(void);
agt_instance_ctrl_t g_timer1_ctrl;
const agt_extended_cfg_t g_timer1_extend =
{
    .count_source            = AGT_CLOCK_LOCO,
    .agto                    = AGT_PIN_CFG_DISABLED,
    .agtoab_settings_b.agtoa = AGT_PIN_CFG_DISABLED,
    .agtoab_settings_b.agtob = AGT_PIN_CFG_DISABLED,
    .measurement_mode        = AGT_MEASURE_DISABLED,
    .agtio_filter            = AGT_AGTIO_FILTER_NONE,
    .enable_pin              = AGT_ENABLE_PIN_NOT_USED,
    .trigger_edge            = AGT_TRIGGER_EDGE_RISING,
    .counter_bit_width       = AGT_COUNTER_BIT_WIDTH_16,
};
const timer_cfg_t g_timer1_cfg =
{
    .mode                = TIMER_MODE_PERIODIC,
    /* Actual period: 2 seconds. Actual duty: 50%. */ .period_counts = (uint32_t) 0x10000, .duty_cycle_counts = 0x8000, .source_div = (timer_source_div_t)0,
    .channel             = 0,
    .p_callback          = ble_abs_hw_timer_callback,
    /** If NULL then do not add & */
#if defined(g_ble_abs0)
    .p_context           = g_ble_abs0,
#else
    .p_context           = &g_ble_abs0,
#endif
    .p_extend            = &g_timer1_extend,
    .cycle_end_ipl       = (7),
#if defined(VECTOR_NUMBER_AGT0_INT)
    .cycle_end_irq       = VECTOR_NUMBER_AGT0_INT,
#else
    .cycle_end_irq       = FSP_INVALID_VECTOR,
#endif
};
/* Instance structure to use this module. */
const timer_instance_t g_timer1 =
{
    .p_ctrl        = &g_timer1_ctrl,
    .p_cfg         = &g_timer1_cfg,
    .p_api         = &g_timer_on_agt
};
gpt_instance_ctrl_t g_timer0_ctrl;
#if 0
const gpt_extended_pwm_cfg_t g_timer0_pwm_extend =
{
    .trough_ipl          = (BSP_IRQ_DISABLED),
#if defined(VECTOR_NUMBER_GPT1_COUNTER_UNDERFLOW)
    .trough_irq          = VECTOR_NUMBER_GPT1_COUNTER_UNDERFLOW,
#else
    .trough_irq          = FSP_INVALID_VECTOR,
#endif
    .poeg_link           = GPT_POEG_LINK_POEG0,
    .output_disable      = (gpt_output_disable_t) ( GPT_OUTPUT_DISABLE_NONE),
    .adc_trigger         = (gpt_adc_trigger_t) ( GPT_ADC_TRIGGER_NONE),
    .dead_time_count_up  = 0,
    .dead_time_count_down = 0,
    .adc_a_compare_match = 0,
    .adc_b_compare_match = 0,
    .interrupt_skip_source = GPT_INTERRUPT_SKIP_SOURCE_NONE,
    .interrupt_skip_count  = GPT_INTERRUPT_SKIP_COUNT_0,
    .interrupt_skip_adc    = GPT_INTERRUPT_SKIP_ADC_NONE,
    .gtioca_disable_setting = GPT_GTIOC_DISABLE_PROHIBITED,
    .gtiocb_disable_setting = GPT_GTIOC_DISABLE_PROHIBITED,
};
#endif
const gpt_extended_cfg_t g_timer0_extend =
{
    .gtioca = { .output_enabled = false,
                .stop_level     = GPT_PIN_LEVEL_LOW
              },
    .gtiocb = { .output_enabled = true,
                .stop_level     = GPT_PIN_LEVEL_LOW
              },
    .start_source        = (gpt_source_t) ( GPT_SOURCE_NONE),
    .stop_source         = (gpt_source_t) ( GPT_SOURCE_NONE),
    .clear_source        = (gpt_source_t) ( GPT_SOURCE_NONE),
    .count_up_source     = (gpt_source_t) ( GPT_SOURCE_NONE),
    .count_down_source   = (gpt_source_t) ( GPT_SOURCE_NONE),
    .capture_a_source    = (gpt_source_t) ( GPT_SOURCE_NONE),
    .capture_b_source    = (gpt_source_t) ( GPT_SOURCE_NONE),
    .capture_a_ipl       = (BSP_IRQ_DISABLED),
    .capture_b_ipl       = (BSP_IRQ_DISABLED),
#if defined(VECTOR_NUMBER_GPT1_CAPTURE_COMPARE_A)
    .capture_a_irq       = VECTOR_NUMBER_GPT1_CAPTURE_COMPARE_A,
#else
    .capture_a_irq       = FSP_INVALID_VECTOR,
#endif
#if defined(VECTOR_NUMBER_GPT1_CAPTURE_COMPARE_B)
    .capture_b_irq       = VECTOR_NUMBER_GPT1_CAPTURE_COMPARE_B,
#else
    .capture_b_irq       = FSP_INVALID_VECTOR,
#endif
     .compare_match_value = { /* CMP_A */ 0x0, /* CMP_B */ 0x0}, .compare_match_status = (0U << 1U) | 0U,
    .capture_filter_gtioca       = GPT_CAPTURE_FILTER_NONE,
    .capture_filter_gtiocb       = GPT_CAPTURE_FILTER_NONE,
#if 0
    .p_pwm_cfg                   = &g_timer0_pwm_extend,
#else
    .p_pwm_cfg                   = NULL,
#endif
#if 0
    .gtior_setting.gtior_b.gtioa  = (0U << 4U) | (0U << 2U) | (0U << 0U),
    .gtior_setting.gtior_b.oadflt = (uint32_t) GPT_PIN_LEVEL_LOW,
    .gtior_setting.gtior_b.oahld  = 0U,
    .gtior_setting.gtior_b.oae    = (uint32_t) false,
    .gtior_setting.gtior_b.oadf   = (uint32_t) GPT_GTIOC_DISABLE_PROHIBITED,
    .gtior_setting.gtior_b.nfaen  = ((uint32_t) GPT_CAPTURE_FILTER_NONE & 1U),
    .gtior_setting.gtior_b.nfcsa  = ((uint32_t) GPT_CAPTURE_FILTER_NONE >> 1U),
    .gtior_setting.gtior_b.gtiob  = (0U << 4U) | (0U << 2U) | (0U << 0U),
    .gtior_setting.gtior_b.obdflt = (uint32_t) GPT_PIN_LEVEL_LOW,
    .gtior_setting.gtior_b.obhld  = 0U,
    .gtior_setting.gtior_b.obe    = (uint32_t) true,
    .gtior_setting.gtior_b.obdf   = (uint32_t) GPT_GTIOC_DISABLE_PROHIBITED,
    .gtior_setting.gtior_b.nfben  = ((uint32_t) GPT_CAPTURE_FILTER_NONE & 1U),
    .gtior_setting.gtior_b.nfcsb  = ((uint32_t) GPT_CAPTURE_FILTER_NONE >> 1U),
#else
    .gtior_setting.gtior = 0U,
#endif
};

const timer_cfg_t g_timer0_cfg =
{
    .mode                = TIMER_MODE_PERIODIC,
    /* Actual period: 0.01 seconds. Actual duty: 50%. */ .period_counts = (uint32_t) 0x75300, .duty_cycle_counts = 0x3a980, .source_div = (timer_source_div_t)0,
    .channel             = 1,
    .p_callback          = r_rf_host_timer_interrupt,
    /** If NULL then do not add & */
#if defined(NULL)
    .p_context           = NULL,
#else
    .p_context           = &NULL,
#endif
    .p_extend            = &g_timer0_extend,
    .cycle_end_ipl       = (2),
#if defined(VECTOR_NUMBER_GPT1_COUNTER_OVERFLOW)
    .cycle_end_irq       = VECTOR_NUMBER_GPT1_COUNTER_OVERFLOW,
#else
    .cycle_end_irq       = FSP_INVALID_VECTOR,
#endif
};
/* Instance structure to use this module. */
const timer_instance_t g_timer0 =
{
    .p_ctrl        = &g_timer0_ctrl,
    .p_cfg         = &g_timer0_cfg,
    .p_api         = &g_timer_on_gpt
};
flash_lp_instance_ctrl_t g_flash0_ctrl;
const flash_cfg_t g_flash0_cfg =
{
    .data_flash_bgo      = false,
    .p_callback          = NULL,
    .p_context           = NULL,
    .ipl                 = (BSP_IRQ_DISABLED),
#if defined(VECTOR_NUMBER_FCU_FRDYI)
    .irq                 = VECTOR_NUMBER_FCU_FRDYI,
#else
    .irq                 = FSP_INVALID_VECTOR,
#endif
};
/* Instance structure to use this module. */
const flash_instance_t g_flash0 =
{
    .p_ctrl        = &g_flash0_ctrl,
    .p_cfg         = &g_flash0_cfg,
    .p_api         = &g_flash_on_flash_lp
};
external_irq_instance_t const *g_ble_external_irq = &g_external_irq0;
flash_instance_t const *g_ble_flash = &g_flash0;

#ifndef ENABLE_HCI_MODE
timer_instance_t const *g_ble_pl_timer = &g_timer0;
#endif

const st_ble_rf_notify_t g_ble_rf_notify = {
    .enable = BLE_CFG_EVENT_NOTIFY_ENABLE_VAL,

    #if ((BLE_CFG_EVENT_NOTIFY_ENABLE_VAL & BLE_EVENT_NOTIFY_START_MASK) != 0)
    .start_cb = r_ble_rf_notify_event_start,
    #endif /* ((BLE_CFG_EVENT_NOTIFY_ENABLE_VAL & BLE_EVENT_NOTIFY_START_MASK) != 0) */
    #if ((BLE_CFG_EVENT_NOTIFY_ENABLE_VAL & BLE_EVENT_NOTIFY_CLOSE_MASK) != 0)
    .close_cb = r_ble_rf_notify_event_close,
    #endif
    #if ((BLE_CFG_EVENT_NOTIFY_ENABLE_VAL & BLE_EVENT_NOTIFY_DS_MASK) != 0)
    .dsleep_cb = r_ble_rf_notify_deep_sleep,
    #endif
};

const uint8_t  g_ble_dbg_pub_addr[6]  = BLE_CFG_RF_DEBUG_PUBLIC_ADDRESS;
const uint8_t  g_ble_dbg_rand_addr[6] = BLE_CFG_RF_DEBUG_RANDOM_ADDRESS;

void ble_host_conn_config(uint32_t ** pp_host_conn_config_table);

/****************************/
/*** Memory customization ***/
/****************************/
#if (BLE_CFG_RF_CONNECTION_MAXIMUM >= 1) && (BLE_CFG_RF_CONNECTION_MAXIMUM <= 7)
const uint16_t g_ble_conn_max = BLE_CFG_RF_CONNECTION_MAXIMUM;
#endif

#if (BLE_CFG_RF_CONNECTION_DATA_MAXIMUM >= 27) && (BLE_CFG_RF_CONNECTION_DATA_MAXIMUM <= 251)
const uint16_t g_ble_conn_data_max = BLE_CFG_RF_CONNECTION_DATA_MAXIMUM;
#endif

#if (BLE_CFG_LIBRARY_TYPE == 0)
#if (BLE_CFG_RF_ADVERTISING_DATA_MAXIMUM >= 31) && (BLE_CFG_RF_ADVERTISING_DATA_MAXIMUM <= 1650)
const uint16_t g_ble_adv_data_max = BLE_CFG_RF_ADVERTISING_DATA_MAXIMUM;
#endif

#if (BLE_CFG_RF_ADVERTISING_SET_MAXIMUM >= 1) && (BLE_CFG_RF_ADVERTISING_SET_MAXIMUM <= 4)
const uint16_t g_ble_adv_set_max = BLE_CFG_RF_ADVERTISING_SET_MAXIMUM;
#endif

#if (BLE_CFG_RF_SYNC_SET_MAXIMUM >= 1) && (BLE_CFG_RF_SYNC_SET_MAXIMUM <= 2)
const uint16_t g_ble_sync_set_max = BLE_CFG_RF_SYNC_SET_MAXIMUM;
#endif
#endif /* (BLE_CFG_LIBRARY_TYPE == 0) */


/******************************************/
/**** LL management data area (2bytes) ****/
/******************************************/
#if (BLE_CFG_LIBRARY_TYPE != 0)
#define BLE_CNTL_DATA_MIN           (392)
#define BLE_CNTL_DATA_CONN          (65)
#define BLE_CNTL_DATA_ADV           (0)
#define BLE_CNTL_DATA_SYNC          (0)
#else /* (BLE_CFG_LIBRARY_TYPE != 0) */
#define BLE_CNTL_DATA_MIN           (279)
#define BLE_CNTL_DATA_CONN          (65)
#define BLE_CNTL_DATA_ADV           (78)
#define BLE_CNTL_DATA_SYNC          (33)
#endif /* (BLE_CFG_LIBRARY_TYPE != 0) */
#define BLE_CNTL_DATA_MAX                           \
(                                                   \
    (BLE_CNTL_DATA_MIN) +                           \
    (BLE_CNTL_DATA_CONN * BLE_CFG_RF_CONNECTION_MAXIMUM) +    \
    (BLE_CNTL_DATA_ADV  * BLE_CFG_RF_ADVERTISING_SET_MAXIMUM) + \
    (BLE_CNTL_DATA_SYNC * BLE_CFG_RF_SYNC_SET_MAXIMUM) +\
    (0)                                             \
)

/******************************************/
/**** BLE stack event heap area (1byte)****/
/******************************************/
#ifdef ENABLE_HCI_MODE
#define BLE_HOST_HEAP_MIN         (0)
#else /* ENABLE_HCI_MODE */
#define BLE_HOST_HEAP_MIN         (3032)
#endif /* ENABLE_HCI_MODE */
#if (BLE_CFG_LIBRARY_TYPE != 0)
#define BLE_CNTL_HEAP_MIN         (88)
#define BLE_CNTL_HEAP_EVENT       (720)
#else /* (BLE_CFG_LIBRARY_TYPE != 0) */
#define BLE_CNTL_HEAP_MIN         (280)
#define BLE_CNTL_HEAP_EVENT       (3784)
#endif /* (BLE_CFG_LIBRARY_TYPE != 0) */
#define BLE_CNTL_HEAP_CONN        (388)
#define _ALIGN_4BYTE(base)        ((((base)+3)>>2)<<2)
#define BLE_CNTL_HEAP_TX_DATA     (_ALIGN_4BYTE(BLE_CFG_RF_CONNECTION_DATA_MAXIMUM+4)+20)
#define BLE_CNTL_HEAP_RX_DATA     (_ALIGN_4BYTE(BLE_CFG_RF_CONNECTION_DATA_MAXIMUM+8)+4)
#define BLE_CNTL_HEAP_TX2_DATA    (BLE_CFG_RF_CONNECTION_DATA_MAXIMUM+8)
#define BLE_CNTL_TXRX_MAX         (4)

#if (BLE_CFG_LIBRARY_TYPE != 0)
#define BLE_CNTL_ADV_DATA_MAX     (0)
#else /* (BLE_CFG_LIBRARY_TYPE != 0) */
#define BLE_ADV_DATA_BLOCKS_LIMIT (36)
#define BLE_ADV_DATA_BLOCKS       ((((BLE_CFG_RF_ADVERTISING_DATA_MAXIMUM + 251)/252) * BLE_CFG_RF_ADVERTISING_SET_MAXIMUM) * 2)
#if (BLE_ADV_DATA_BLOCKS > BLE_ADV_DATA_BLOCKS_LIMIT)
#define BLE_CNTL_ADV_DATA_MAX     (BLE_ADV_DATA_BLOCKS_LIMIT * 256)
#else /* (BLE_ADV_DATA_BLOCKS > BLE_ADV_DATA_BLOCKS_LIMIT) */
#define BLE_CNTL_ADV_DATA_MAX     (BLE_ADV_DATA_BLOCKS * 256)
#endif /* (BLE_ADV_DATA_BLOCKS > BLE_ADV_DATA_BLOCKS_LIMIT) */
#endif /* (BLE_CFG_LIBRARY_TYPE != 0) */

#define BLE_CNTL_HEAP_MAX                           \
(                                                   \
    (BLE_CNTL_HEAP_MIN) +                           \
    (BLE_HOST_HEAP_MIN) +                           \
    (BLE_CNTL_HEAP_EVENT) +                         \
    (BLE_CNTL_HEAP_CONN * BLE_CFG_RF_CONNECTION_MAXIMUM) +    \
    (BLE_CNTL_HEAP_TX_DATA * BLE_CNTL_TXRX_MAX) +   \
    (BLE_CNTL_HEAP_RX_DATA * BLE_CNTL_TXRX_MAX) +   \
    (BLE_CNTL_ADV_DATA_MAX) +                       \
    (0)                                             \
)


/******************************************/
/**** LL connection entry area (1byte) ****/
/******************************************/
#if (BLE_CFG_LIBRARY_TYPE == 1)
#define BLE_CNTL_CONN_ENT         (328)
#elif (BLE_CFG_LIBRARY_TYPE == 2)
#define BLE_CNTL_CONN_ENT         (316)
#else /* (BLE_CFG_LIBRARY_TYPE == x) */
#define BLE_CNTL_CONN_ENT         (336)
#endif /* (BLE_CFG_LIBRARY_TYPE == x) */
#define BLE_CNTL_CONN_ENT_MAX                       \
(                                                   \
    (BLE_CNTL_CONN_ENT * BLE_CFG_RF_CONNECTION_MAXIMUM) +     \
    (0)                                             \
)

/******************************************/
/**** LL Advertising set area (1byte)  ****/
/******************************************/
#define BLE_CNTL_ADV_SET          (152)
#define BLE_CNTL_ADV_SET_MAX                        \
(                                                   \
    (BLE_CNTL_ADV_SET * BLE_CFG_RF_ADVERTISING_SET_MAXIMUM) +   \
    (0)                                             \
)

uint16_t g_ble_cntl_data[BLE_CNTL_DATA_MAX];
uint32_t g_ble_cntl_heap[(BLE_CNTL_HEAP_MAX+3)/4];
uint32_t g_ble_cntl_heap2[(BLE_CNTL_HEAP_TX2_DATA+3)/4];
uint32_t g_ble_cntl_conn_ent[(BLE_CNTL_CONN_ENT_MAX+3)/4];
#if (BLE_CFG_LIBRARY_TYPE == 0)
uint32_t g_ble_cntl_adv_set[(BLE_CNTL_ADV_SET_MAX+3)/4];
const uint16_t g_ble_adv_block         = (uint16_t)(BLE_CNTL_ADV_DATA_MAX/256);
#endif /* (BLE_CFG_LIBRARY_TYPE == 0) */

#ifdef NO_USE_BSP
const uint16_t g_ble_main_clk_khz      = (uint16_t)BLE_CFG_MCU_MAIN_CLK_KHZ;
const ble_mcu_clock_change_cb_t g_ble_mcu_clock_change_fp = NULL;
#else /* NO_USE_BSP */

#include "bsp_cfg.h"
#if (BSP_CFG_CLKOUT_RF_MAIN == 1) && (BSP_CFG_XTAL_HZ == 4000000) && (BLE_CFG_RF_CLKOUT_EN == 5)
extern void R_BSP_ConfigClockSetting(void);
const uint16_t g_ble_main_clk_khz      = (uint16_t)(BSP_CFG_XTAL_HZ/1000);
const ble_mcu_clock_change_cb_t g_ble_mcu_clock_change_fp = R_BSP_ConfigClockSetting;
#elif (BSP_CFG_CLKOUT_RF_MAIN == 0)
const uint16_t g_ble_main_clk_khz      = (uint16_t)BLE_CFG_MCU_MAIN_CLK_KHZ;
const ble_mcu_clock_change_cb_t g_ble_mcu_clock_change_fp = NULL;

#endif /* (BSP_CFG_CLKOUT_RF_MAIN == 1) && (BSP_CFG_XTAL_HZ == 4000000) && (BLE_CFG_RF_CLKOUT_EN == 5)  */
#endif /* NO_USE_BSP */

#define BLE_DEVICE_DATA_CF_BLOCK_BASE                         (0x00000000)
#define BLE_DEVICE_DATA_DF_BLOCK_BASE                         (0x40100000)

#if (BSP_FEATURE_FLASH_HP_DF_BLOCK_SIZE != 0)
 #define BLE_DEVICE_DATA_CF_BLOCK_SIZE                        (BSP_FEATURE_FLASH_HP_CF_BLOCK_SIZE)
 #define BLE_DEVICE_DATA_DF_BLOCK_SIZE                        (BSP_FEATURE_FLASH_HP_DF_BLOCK_SIZE)
#elif (BSP_FEATURE_FLASH_LP_DF_BLOCK_SIZE != 0)
 #define BLE_DEVICE_DATA_CF_BLOCK_SIZE                        (BSP_FEATURE_FLASH_LP_CF_BLOCK_SIZE)
 #define BLE_DEVICE_DATA_DF_BLOCK_SIZE                        (BSP_FEATURE_FLASH_LP_DF_BLOCK_SIZE)
#endif

#if (BLE_CFG_DEV_DATA_CF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_CF_BLOCK <= 255)
const uint32_t  g_ble_dev_data_cf_addr = BLE_DEVICE_DATA_CF_BLOCK_BASE + BLE_DEVICE_DATA_CF_BLOCK_SIZE * BLE_CFG_DEV_DATA_CF_BLOCK;
#else /* (BLE_CFG_DEV_DATA_CF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_CF_BLOCK <= 255) */
const uint32_t  g_ble_dev_data_cf_addr = 0U;
#endif /* (BLE_CFG_DEV_DATA_CF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_CF_BLOCK <= 255) */

#if (BLE_CFG_DEV_DATA_DF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_DF_BLOCK <= 7)
const uint32_t  g_ble_dev_data_df_addr = BLE_DEVICE_DATA_DF_BLOCK_BASE + BLE_DEVICE_DATA_DF_BLOCK_SIZE * BLE_CFG_DEV_DATA_DF_BLOCK;
#else /* (BLE_CFG_DEV_DATA_DF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_DF_BLOCK <= 7) */
const uint32_t  g_ble_dev_data_df_addr = 0U;
#endif /* (BLE_CFG_DEV_DATA_DF_BLOCK >= 0) && (BLE_CFG_DEV_DATA_DF_BLOCK <= 7) */

const uint8_t g_ble_rf_config[] =
{
/***************************************/
/**** CLVAL setting                 ****/
/***************************************/
#if (BLE_CFG_RF_CLVAL >= 0) && (BLE_CFG_RF_CLVAL <= 15)
    (BLE_CFG_RF_CLVAL << 0) |
#endif /* BLE_CFG_RF_CLVAL */
    0x00, /* base value */

/***************************************/
/**** RF Slow Clock setting         ****/
/***************************************/
#if (BLE_CFG_RF_EXT32K_EN >= 0) && (BLE_CFG_RF_EXT32K_EN <= 1)
    /**** External 32kHz setting ****/
    (BLE_CFG_RF_EXT32K_EN << 0) |
#endif /* BLE_CFG_RF_EXT32K_EN */

#if (BLE_CFG_RF_MCU_CLKOUT_FREQ >= 0) && (BLE_CFG_RF_MCU_CLKOUT_FREQ <= 1)
    /**** MCU CLKOUT setting ****/
    (BLE_CFG_RF_MCU_CLKOUT_FREQ << 1) |
#endif /* BLE_CFG_RF_MCU_CLKOUT_FREQ */

#if (BLE_CFG_RF_MCU_CLKOUT_PORT >= 0) && (BLE_CFG_RF_MCU_CLKOUT_PORT <= 1)
    /**** RF_LOCO setting ****/
    (BLE_CFG_RF_MCU_CLKOUT_PORT << 2) |
#endif /* BLE_RF_CONF_RF_LOCO */

/**** Sleep Clock Accuracy (SCA) setting ****/
#if   (BLE_CFG_RF_SCA>=251) && (BLE_CFG_RF_SCA<=500)
    (0x00 << 4) |
#elif (BLE_CFG_RF_SCA>=151) && (BLE_CFG_RF_SCA<=250)
    (0x01 << 4) |
#elif (BLE_CFG_RF_SCA>=101) && (BLE_CFG_RF_SCA<=150)
    (0x02 << 4) |
#elif (BLE_CFG_RF_SCA>=76 ) && (BLE_CFG_RF_SCA<=100)
    (0x03 << 4) |
#elif (BLE_CFG_RF_SCA>=51 ) && (BLE_CFG_RF_SCA<=75 )
    (0x04 << 4) |
#elif (BLE_CFG_RF_SCA>=31 ) && (BLE_CFG_RF_SCA<=50 )
    (0x05 << 4) |
#elif (BLE_CFG_RF_SCA>=21 ) && (BLE_CFG_RF_SCA<=30 )
    (0x06 << 4) |
#elif (BLE_CFG_RF_SCA>=0  ) && (BLE_CFG_RF_SCA<=20 )
    (0x07 << 4) |
#endif /* BLE_CFG_RF_SCA */
    0x00, /* base value */

/***************************************/
/**** Tx Power setting              ****/
/***************************************/
#if (BLE_CFG_RF_MAX_TX_POW >= 0) && (BLE_CFG_RF_MAX_TX_POW <= 2)
    /**** Defalut Tx Power Setting ****/
    (BLE_CFG_RF_MAX_TX_POW << 0) |
#endif /* BLE_CFG_RF_MAX_TX_POW */
    0x00, /* base value */

/***************************************/
/**** RF option setting             ****/
/***************************************/
#if (BLE_CFG_RF_DCDC_CONVERTER_ENABLE >= 0) && (BLE_CFG_RF_DCDC_CONVERTER_ENABLE <= 1 )

    /**** DC-DC converter setting ****/
    (BLE_CFG_RF_DCDC_CONVERTER_ENABLE << 0) |

#endif /* BLE_CFG_RF_DCDC_CONVERTER_ENABLE */
#if (BLE_CFG_RF_DEF_TX_POW >= 0) && (BLE_CFG_RF_DEF_TX_POW <= 1)
    /**** Max Tx Power Setting ****/
    (BLE_CFG_RF_DEF_TX_POW << 1) |
#endif /* BLE_CFG_RF_DEF_TX_POW */
#if (BLE_CFG_RF_CLKOUT_EN >= 0) && (BLE_CFG_RF_CLKOUT_EN <= 7)
    /**** RF clock output settng ****/
    (BLE_CFG_RF_CLKOUT_EN << 4) |
#endif /* BLE_CFG_RF_CLKOUT_EN */

    0x00 /* base value */
};

/***************************************/
/**** Host Stack settings           ****/
/***************************************/
#ifndef ENABLE_HCI_MODE
#define BLE_HOST_L2_SIG_TBL_LEN                  24
#define BLE_HOST_L2_CH_PARAM_TBL_LEN              2
#define BLE_HOST_HCI_REM_TBL_LEN                  6
#define BLE_HOST_SMP_CONFIG_LEN                 108
#define BLE_HOST_GAP_CONN_TBL_LEN                12
#define BLE_HOST_DEV_Q_TBL_LEN                   14
#define BLE_HOST_ATT_CONN_TBL_LEN                16
#define BLE_HOST_GATTS_CNF_TBL_LEN                2

uint32_t g_ble_host_dev_q_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_DEV_Q_TBL_LEN + 3)/4];
uint32_t g_ble_host_hci_rem_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_HCI_REM_TBL_LEN + 3)/4];
uint32_t g_ble_host_l2_sig_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_L2_SIG_TBL_LEN + 3)/4];
uint32_t g_ble_host_l2_ch_param_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_L2_CH_PARAM_TBL_LEN + 3)/4];
uint32_t g_ble_host_smp_config_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_SMP_CONFIG_LEN + 3)/4];
uint32_t g_ble_host_att_conn_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_ATT_CONN_TBL_LEN + 3)/4];
uint32_t g_ble_host_gap_conn_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_GAP_CONN_TBL_LEN + 3)/4];
uint32_t g_ble_host_gatts_cnf_tbl[(BLE_CFG_RF_CONNECTION_MAXIMUM * BLE_HOST_GATTS_CNF_TBL_LEN + 3)/4];

const uint32_t g_p_ble_host_config_tbls[] = {
    (uint32_t)g_ble_host_dev_q_tbl,
    (uint32_t)g_ble_host_hci_rem_tbl,
    (uint32_t)g_ble_host_l2_sig_tbl,
    (uint32_t)g_ble_host_l2_ch_param_tbl,
    (uint32_t)g_ble_host_smp_config_tbl,
    (uint32_t)g_ble_host_att_conn_tbl,
    (uint32_t)g_ble_host_gap_conn_tbl,
    (uint32_t)g_ble_host_gatts_cnf_tbl
};

void ble_host_conn_config(uint32_t ** pp_host_conn_config_table)
{
    * pp_host_conn_config_table = (uint32_t*)g_p_ble_host_config_tbls;
}
#endif /* !ENABLE_HCI_MODE */

/***************************************/
/**** Data Flash Usage              ****/
/***************************************/

#if (BLE_CFG_DEV_DATA_DF_BLOCK >= 0) || \
    ( (BLE_CFG_EN_SEC_DATA != 0) && (BLE_CFG_SECD_DATA_DF_BLOCK >= 0) )
uint32_t    g_ble_flash_enable = 1;
#else
uint32_t    g_ble_flash_enable = 0;
#endif
static ble_abs_pairing_parameter_t gs_abs_pairing_param =
{
 .io_capabilitie_local_device = BLE_GAP_IOCAP_NOINPUT_NOOUTPUT,
 .mitm_protection_policy      = BLE_GAP_SEC_MITM_BEST_EFFORT,
 .secure_connection_only      = BLE_GAP_SC_BEST_EFFORT,
 .local_key_distribute        = (uint8_t)( 0),
 .remote_key_distribute       = (uint8_t)( 0),
 .maximum_key_size            = 16,
};

ble_abs_instance_ctrl_t g_ble_abs0_ctrl;

const ble_abs_cfg_t g_ble_abs0_cfg =
{
    .gap_callback = gap_cb,
    .vendor_specific_callback = vs_cb,
    .p_pairing_parameter = &gs_abs_pairing_param,
    .p_gatt_server_callback_list = gs_abs_gatts_cb_param,
    .gatt_server_callback_list_number = 2,
    .p_gatt_client_callback_list = gs_abs_gattc_cb_param,
    .gatt_client_callback_list_number = 2,
    .p_flash_instance = &g_flash0,
    .p_timer_instance = &g_timer1,
    .p_callback = NULL,
    .p_context = NULL,
    .p_extend = NULL,
};

/* Instance structure to use this module. */
const ble_abs_instance_t g_ble_abs0 =
{
    .p_ctrl        = &g_ble_abs0_ctrl,
    .p_cfg         = &g_ble_abs0_cfg,
    .p_api         = &g_ble_abs_on_ble
};

extern uint32_t g_fsp_common_thread_count;

                const rm_freertos_port_parameters_t ble_core_task_parameters =
                {
                    .p_context = (void *) NULL,
                };

                void ble_core_task_create (void)
                {
                    /* Increment count so we will know the number of threads created in the RA Configuration editor. */
                    g_fsp_common_thread_count++;

                    /* Initialize each kernel object. */
                    

                    #if 1
                    ble_core_task = xTaskCreateStatic(
                    #else
                    BaseType_t ble_core_task_create_err = xTaskCreate(
                    #endif
                        ble_core_task_func,
                        (const char *)"BLE_CORE_TASK",
                        2048/4, // In words, not bytes
                        (void *) &ble_core_task_parameters, //pvParameters
                        4,
                        #if 1
                        (StackType_t *)&ble_core_task_stack,
                        (StaticTask_t *)&ble_core_task_memory
                        #else
                        & ble_core_task
                        #endif
                    );

                    #if 1
                    if (NULL == ble_core_task)
                    {
                        rtos_startup_err_callback(ble_core_task, 0);
                    }
                    #else
                    if (pdPASS != ble_core_task_create_err)
                    {
                        rtos_startup_err_callback(ble_core_task, 0);
                    }
                    #endif
                }
                static void ble_core_task_func (void * pvParameters)
                {
                    /* Initialize common components */
                    rtos_startup_common_init();

                    /* Initialize each module instance. */
                    

                    #if (1 == BSP_TZ_NONSECURE_BUILD) && (1 == 1)
                    /* When FreeRTOS is used in a non-secure TrustZone application, portALLOCATE_SECURE_CONTEXT must be called prior
                     * to calling any non-secure callable function in a thread. The parameter is unused in the FSP implementation.
                     * If no slots are available then configASSERT() will be called from vPortSVCHandler_C(). If this occurs, the
                     * application will need to either increase the value of the "Process Stack Slots" Property in the rm_tz_context
                     * module in the secure project or decrease the number of threads in the non-secure project that are allocating
                     * a secure context. Users can control which threads allocate a secure context via the Properties tab when
                     * selecting each thread. Note that the idle thread in FreeRTOS requires a secure context so the application
                     * will need at least 1 secure context even if no user threads make secure calls. */
                     portALLOCATE_SECURE_CONTEXT(0);
                    #endif

                    /* Enter user code for this thread. Pass task handle. */
                    ble_core_task_entry(pvParameters);
                }
