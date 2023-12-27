#ifndef MICROPY_INCLUDED_MACHINE_ADCBLOCK_H
#define MICROPY_INCLUDED_MACHINE_ADCBLOCK_H

#include "esp_adc_cal.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"

#if CONFIG_IDF_TARGET_ESP32 || CONFIG_IDF_TARGET_ESP32S2 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32S3
// See #if above in "esp_adc_cal_types_legacy.h"
#define HAS_ADC_CAL
#endif

#define ADC_ATTEN_MAX SOC_ADC_ATTEN_NUM

typedef struct _madcblock_obj_t {
    mp_obj_base_t base;
    adc_unit_t unit_id;
    mp_int_t bits;
    adc_bits_width_t width;
    #ifdef HAS_ADC_CAL
    esp_adc_cal_characteristics_t *characteristics[ADC_ATTEN_MAX];
    #else
    int *characteristics[1];
    #endif
} madcblock_obj_t;

extern madcblock_obj_t madcblock_obj[];

extern void madcblock_bits_helper(madcblock_obj_t *self, mp_int_t bits);
extern mp_int_t madcblock_read_helper(madcblock_obj_t *self, adc_channel_t channel_id);
extern mp_int_t madcblock_read_uv_helper(madcblock_obj_t *self, adc_channel_t channel_id, adc_atten_t atten);

#endif // MICROPY_INCLUDED_MACHINE_ADCBLOCK_H
