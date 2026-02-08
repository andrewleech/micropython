// Stub header to declare BT firmware data as extern
// (actual definition is in cybt_shared_bus.c)
extern const unsigned char cyw43_btfw_43439[];
extern const unsigned int cyw43_btfw_43439_len;

// Alias names used in cyw43_bthci_uart.c
#define btfw_data cyw43_btfw_43439
#define btfw_len cyw43_btfw_43439_len
