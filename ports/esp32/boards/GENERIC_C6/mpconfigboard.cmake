set(IDF_TARGET esp32c6)

set(SDKCONFIG_DEFAULTS
    boards/sdkconfig.base
    #boards/sdkconfig.ble
    boards/GENERIC_C6/sdkconfig.board
)

#set(CONFIG_BT_NIMBLE_LEGACY_VHCI_ENABLE "y")
