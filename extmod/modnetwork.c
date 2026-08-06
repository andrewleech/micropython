/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2014 Damien P. George
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include "py/objlist.h"
#include "py/objtuple.h"
#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_PY_NETWORK

#include "shared/netutils/netutils.h"
#include "extmod/modnetwork.h"

#if MICROPY_PY_NETWORK_CYW43
// So that CYW43_LINK_xxx constants are available to MICROPY_PORT_NETWORK_INTERFACES.
#include "lib/cyw43-driver/src/cyw43.h"
extern const struct _mp_obj_type_t mp_network_cyw43_type;
#endif

#if MICROPY_PY_NETWORK_WIZNET5K
extern const struct _mp_obj_type_t mod_network_nic_type_wiznet5k;
#endif

#if MICROPY_PY_NETWORK_NINAW10
extern const struct _mp_obj_type_t mod_network_nic_type_nina;
#endif

#if MICROPY_PY_NETWORK_ESP_HOSTED
extern const struct _mp_obj_type_t mod_network_esp_hosted_type;
#endif

#ifdef MICROPY_PY_NETWORK_INCLUDEFILE
#include MICROPY_PY_NETWORK_INCLUDEFILE
#endif

#if MICROPY_PY_LWIP && LWIP_MDNS_RESPONDER
#include "lwip/netif.h"
#include "lwip/apps/mdns.h"
// lwIP is not re-entrant; on ports where it runs at raised priority these
// macros take/release the lwIP lock around API calls. Default to no-ops.
#ifndef MICROPY_PY_LWIP_ENTER
#define MICROPY_PY_LWIP_ENTER
#define MICROPY_PY_LWIP_EXIT
#endif
#endif

/// \module network - network configuration
///
/// This module provides network drivers and routing configuration.

char mod_network_country_code[2] = "XX";

#ifndef MICROPY_PY_NETWORK_HOSTNAME_DEFAULT
#error "MICROPY_PY_NETWORK_HOSTNAME_DEFAULT must be set in mpconfigport.h or mpconfigboard.h"
#endif

char mod_network_hostname_data[MICROPY_PY_NETWORK_HOSTNAME_MAX_LEN + 1] = MICROPY_PY_NETWORK_HOSTNAME_DEFAULT;

#ifdef MICROPY_PORT_NETWORK_INTERFACES

void mod_network_init(void) {
    mp_obj_list_init(&MP_STATE_PORT(mod_network_nic_list), 0);
}

void mod_network_deinit(void) {
    #if !MICROPY_PY_LWIP
    for (mp_uint_t i = 0; i < MP_STATE_PORT(mod_network_nic_list).len; i++) {
        mp_obj_t nic = MP_STATE_PORT(mod_network_nic_list).items[i];
        const mod_network_nic_protocol_t *nic_protocol = MP_OBJ_TYPE_GET_SLOT(mp_obj_get_type(nic), protocol);
        if (nic_protocol->deinit) {
            nic_protocol->deinit();
        }
    }
    #endif
}

void mod_network_register_nic(mp_obj_t nic) {
    for (mp_uint_t i = 0; i < MP_STATE_PORT(mod_network_nic_list).len; i++) {
        if (MP_STATE_PORT(mod_network_nic_list).items[i] == nic) {
            // nic already registered
            return;
        }
    }
    // nic not registered so add to list
    mp_obj_list_append(MP_OBJ_FROM_PTR(&MP_STATE_PORT(mod_network_nic_list)), nic);
}

mp_obj_t mod_network_find_nic(const uint8_t *ip) {
    // find a NIC that is suited to given IP address
    for (mp_uint_t i = 0; i < MP_STATE_PORT(mod_network_nic_list).len; i++) {
        mp_obj_t nic = MP_STATE_PORT(mod_network_nic_list).items[i];
        // TODO check IP suitability here
        // mod_network_nic_protocol_t *nic_protocol = (mod_network_nic_protocol_t *)MP_OBJ_TYPE_GET_SLOT(mp_obj_get_type(nic), protocol);
        return nic;
    }

    mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no available NIC"));
}

static mp_obj_t network_route(void) {
    return MP_OBJ_FROM_PTR(&MP_STATE_PORT(mod_network_nic_list));
}
static MP_DEFINE_CONST_FUN_OBJ_0(network_route_obj, network_route);

MP_REGISTER_ROOT_POINTER(mp_obj_list_t mod_network_nic_list);

#endif // MICROPY_PORT_NETWORK_INTERFACES

static mp_obj_t network_country(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        return mp_obj_new_str(mod_network_country_code, 2);
    } else {
        size_t len;
        const char *str = mp_obj_str_get_data(args[0], &len);
        if (len != 2) {
            mp_raise_ValueError(NULL);
        }
        mod_network_country_code[0] = str[0];
        mod_network_country_code[1] = str[1];
        return mp_const_none;
    }
}
// TODO: Non-static to allow backwards-compatible pyb.country.
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_network_country_obj, 0, 1, network_country);

mp_obj_t mod_network_hostname(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        return mp_obj_new_str_from_cstr(mod_network_hostname_data);
    } else {
        size_t len;
        const char *str = mp_obj_str_get_data(args[0], &len);
        if (len > MICROPY_PY_NETWORK_HOSTNAME_MAX_LEN) {
            mp_raise_ValueError(NULL);
        }
        memcpy(mod_network_hostname_data, str, len);
        mod_network_hostname_data[len] = 0;
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_network_hostname_obj, 0, 1, mod_network_hostname);

#if MICROPY_PY_LWIP && LWIP_MDNS_RESPONDER

// mDNS service advertising, exposing the lwIP responder service API.
//
// The TXT items for each service slot are stored as a tuple of `bytes` objects
// kept alive via a GC root pointer (mod_network_mdns_txt) so the lwIP txt
// callback can read them. lwIP stores the slot index as the service's
// txt_userdata and passes it back to the callback. Using GC-managed objects
// (rather than libc malloc, which has no usable heap on some ports) keeps the
// data reachable and avoids a separate allocator.

// Resolve the netif that has an active mDNS responder. Prefer the default
// netif, otherwise the first netif in the list with mDNS active. Must be
// called with the lwIP lock held; returns NULL if no responder is active (the
// caller raises after releasing the lock).
static struct netif *network_mdns_netif(void) {
    if (netif_default != NULL && mdns_resp_netif_active(netif_default)) {
        return netif_default;
    }
    for (struct netif *netif = netif_list; netif != NULL; netif = netif->next) {
        if (mdns_resp_netif_active(netif)) {
            return netif;
        }
    }
    return NULL;
}

// lwIP callback: emit the stored TXT items for the given service. Runs at lwIP
// (timer) priority while building a TXT reply.
static void network_mdns_txt_cb(struct mdns_service *service, void *txt_userdata) {
    int slot = (int)(intptr_t)txt_userdata;
    if (slot < 0 || slot >= MDNS_MAX_SERVICES) {
        return;
    }
    mp_obj_t items_obj = MP_STATE_PORT(mod_network_mdns_txt)[slot];
    if (items_obj == MP_OBJ_NULL) {
        return;
    }
    size_t n_items;
    mp_obj_t *items;
    mp_obj_tuple_get(items_obj, &n_items, &items);
    for (size_t i = 0; i < n_items; i++) {
        size_t len;
        const char *item = mp_obj_str_get_data(items[i], &len);
        if (len > 0 && len <= MDNS_LABEL_MAXLEN) {
            mdns_resp_add_service_txtitem(service, item, (uint8_t)len);
        }
    }
}

// network.mdns_add_service(instance, service, proto, port, txt=None) -> slot
static mp_obj_t network_mdns_add_service(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_instance, ARG_service, ARG_proto, ARG_port, ARG_txt };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_instance, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_service, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_proto, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_port, MP_ARG_REQUIRED | MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_txt, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
    };
    mp_arg_val_t parsed[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, parsed);

    const char *instance = mp_obj_str_get_str(parsed[ARG_instance].u_obj);
    const char *service = mp_obj_str_get_str(parsed[ARG_service].u_obj);
    const char *proto_str = mp_obj_str_get_str(parsed[ARG_proto].u_obj);
    mp_int_t port = parsed[ARG_port].u_int;

    enum mdns_sd_proto proto;
    if (strcmp(proto_str, "tcp") == 0) {
        proto = DNSSD_PROTO_TCP;
    } else if (strcmp(proto_str, "udp") == 0) {
        proto = DNSSD_PROTO_UDP;
    } else {
        mp_raise_ValueError(MP_ERROR_TEXT("proto must be 'tcp' or 'udp'"));
    }

    // Collect TXT items into a tuple of `bytes`. Accept a dict of
    // {key: value} -> "key=value", or an iterable of "key=value" strings. The
    // tuple is kept alive via the mod_network_mdns_txt root pointer so the txt
    // callback can read it; using GC-managed objects avoids needing a separate
    // C allocator (which has no usable heap on some ports).
    mp_obj_t txt = parsed[ARG_txt].u_obj;
    mp_obj_t items_tuple = mp_const_empty_tuple;
    if (txt != mp_const_none) {
        mp_obj_t item_list = mp_obj_new_list(0, NULL);
        if (mp_obj_is_type(txt, &mp_type_dict)) {
            mp_map_t *map = mp_obj_dict_get_map(txt);
            for (size_t i = 0; i < map->alloc; i++) {
                if (mp_map_slot_is_filled(map, i)) {
                    vstr_t vstr;
                    vstr_init(&vstr, 16);
                    vstr_add_str(&vstr, mp_obj_str_get_str(map->table[i].key));
                    vstr_add_byte(&vstr, '=');
                    vstr_add_str(&vstr, mp_obj_str_get_str(map->table[i].value));
                    mp_obj_list_append(item_list, mp_obj_new_bytes((const byte *)vstr.buf, vstr.len));
                    vstr_clear(&vstr);
                }
            }
        } else {
            mp_obj_t iterable = mp_getiter(txt, NULL);
            mp_obj_t item;
            while ((item = mp_iternext(iterable)) != MP_OBJ_STOP_ITERATION) {
                size_t len;
                const char *s = mp_obj_str_get_data(item, &len);
                mp_obj_list_append(item_list, mp_obj_new_bytes((const byte *)s, len));
            }
        }
        size_t list_len;
        mp_obj_t *list_items;
        mp_obj_list_get(item_list, &list_len, &list_items);
        items_tuple = mp_obj_new_tuple(list_len, list_items);
    }

    // Find the first free local slot. lwIP allocates slots lowest-first the
    // same way, so the slot it returns matches; we pass the slot index as the
    // service's txt_userdata so the callback can find the stored TXT items.
    int slot = -1;
    for (int i = 0; i < MDNS_MAX_SERVICES; i++) {
        if (MP_STATE_PORT(mod_network_mdns_txt)[i] == MP_OBJ_NULL) {
            slot = i;
            break;
        }
    }

    s8_t lwip_slot = -1;
    bool no_netif = false;
    // Publish the slot's TXT tuple and register the service atomically under
    // the lwIP lock: the txt callback runs at lwIP (timer) priority and reads
    // mod_network_mdns_txt[slot], so it must be in place before the service is
    // registered. lwip_slot should equal our pre-scanned slot under NO_SYS
    // since this is the only code path that adds services; roll back on
    // mismatch.
    MICROPY_PY_LWIP_ENTER
    struct netif *netif = network_mdns_netif();
    if (netif == NULL) {
        no_netif = true;
    } else if (slot >= 0) {
        MP_STATE_PORT(mod_network_mdns_txt)[slot] = items_tuple;
        lwip_slot = mdns_resp_add_service(netif, instance, service, proto, port,
            network_mdns_txt_cb, (void *)(intptr_t)slot);
        if (lwip_slot != slot) {
            if (lwip_slot >= 0) {
                mdns_resp_del_service(netif, lwip_slot);
            }
            MP_STATE_PORT(mod_network_mdns_txt)[slot] = MP_OBJ_NULL;
            lwip_slot = -1;
        }
    }
    MICROPY_PY_LWIP_EXIT

    if (no_netif || slot < 0 || lwip_slot < 0) {
        mp_raise_OSError(no_netif ? MP_ENODEV : MP_EINVAL);
    }

    return MP_OBJ_NEW_SMALL_INT(slot);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(network_mdns_add_service_obj, 4, network_mdns_add_service);

// network.mdns_remove_service(slot)
static mp_obj_t network_mdns_remove_service(mp_obj_t slot_in) {
    mp_int_t slot = mp_obj_get_int(slot_in);
    if (slot < 0 || slot >= MDNS_MAX_SERVICES) {
        mp_raise_ValueError(MP_ERROR_TEXT("invalid slot"));
    }
    bool no_netif = false;
    err_t err = ERR_OK;
    MICROPY_PY_LWIP_ENTER
    struct netif *netif = network_mdns_netif();
    if (netif == NULL) {
        no_netif = true;
    } else {
        err = mdns_resp_del_service(netif, slot);
    }
    MP_STATE_PORT(mod_network_mdns_txt)[slot] = MP_OBJ_NULL;
    MICROPY_PY_LWIP_EXIT
    if (no_netif) {
        mp_raise_OSError(MP_ENODEV);
    }
    if (err != ERR_OK) {
        mp_raise_OSError(MP_EINVAL);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(network_mdns_remove_service_obj, network_mdns_remove_service);

// network.mdns_announce()
static mp_obj_t network_mdns_announce(void) {
    bool no_netif = false;
    MICROPY_PY_LWIP_ENTER
    struct netif *netif = network_mdns_netif();
    if (netif == NULL) {
        no_netif = true;
    } else {
        mdns_resp_announce(netif);
    }
    MICROPY_PY_LWIP_EXIT
    if (no_netif) {
        mp_raise_OSError(MP_ENODEV);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(network_mdns_announce_obj, network_mdns_announce);

// TXT items (a tuple of `bytes` per service slot) advertised by the lwIP mDNS
// responder. Kept reachable for the GC and readable from the txt callback.
MP_REGISTER_ROOT_POINTER(mp_obj_t mod_network_mdns_txt[MDNS_MAX_SERVICES]);

#endif // MICROPY_PY_LWIP && LWIP_MDNS_RESPONDER

#if LWIP_VERSION_MAJOR >= 2
MP_DEFINE_CONST_FUN_OBJ_KW(mod_network_ipconfig_obj, 0, mod_network_ipconfig);
#endif
#if MICROPY_PY_NETWORK_NINAW10
MP_DEFINE_CONST_FUN_OBJ_KW(mod_network_ipconfig_obj, 0, network_ninaw10_ipconfig);
#endif

static const mp_rom_map_elem_t mp_module_network_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_network) },
    { MP_ROM_QSTR(MP_QSTR_country), MP_ROM_PTR(&mod_network_country_obj) },
    { MP_ROM_QSTR(MP_QSTR_hostname), MP_ROM_PTR(&mod_network_hostname_obj) },
    #if MICROPY_PY_LWIP && LWIP_MDNS_RESPONDER
    { MP_ROM_QSTR(MP_QSTR_mdns_add_service), MP_ROM_PTR(&network_mdns_add_service_obj) },
    { MP_ROM_QSTR(MP_QSTR_mdns_remove_service), MP_ROM_PTR(&network_mdns_remove_service_obj) },
    { MP_ROM_QSTR(MP_QSTR_mdns_announce), MP_ROM_PTR(&network_mdns_announce_obj) },
    #endif
    #if LWIP_VERSION_MAJOR >= 2 || MICROPY_PY_NETWORK_NINAW10
    { MP_ROM_QSTR(MP_QSTR_ipconfig), MP_ROM_PTR(&mod_network_ipconfig_obj) },
    #endif

    #if MICROPY_PY_NETWORK_PPP_LWIP
    { MP_ROM_QSTR(MP_QSTR_PPP), MP_ROM_PTR(&mp_network_ppp_lwip_type) },
    #endif

    // Defined per port in mpconfigport.h
    #ifdef MICROPY_PORT_NETWORK_INTERFACES
    { MP_ROM_QSTR(MP_QSTR_route), MP_ROM_PTR(&network_route_obj) },
    MICROPY_PORT_NETWORK_INTERFACES
    #endif

    #if MICROPY_PY_NETWORK_CYW43
    { MP_ROM_QSTR(MP_QSTR_WLAN), MP_ROM_PTR(&mp_network_cyw43_type) },
    // CYW43 status constants, currently for rp2 port only.
    // TODO move these to WIFI module for all ports.
    #if defined(PICO_PROGRAM_NAME) && defined(CYW43_LINK_DOWN)
    { MP_ROM_QSTR(MP_QSTR_STAT_IDLE), MP_ROM_INT(CYW43_LINK_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_STAT_CONNECTING), MP_ROM_INT(CYW43_LINK_JOIN) },
    { MP_ROM_QSTR(MP_QSTR_STAT_WRONG_PASSWORD), MP_ROM_INT(CYW43_LINK_BADAUTH) },
    { MP_ROM_QSTR(MP_QSTR_STAT_NO_AP_FOUND), MP_ROM_INT(CYW43_LINK_NONET) },
    { MP_ROM_QSTR(MP_QSTR_STAT_CONNECT_FAIL), MP_ROM_INT(CYW43_LINK_FAIL) },
    { MP_ROM_QSTR(MP_QSTR_STAT_GOT_IP), MP_ROM_INT(CYW43_LINK_UP) },
    #endif
    #endif

    #if MICROPY_PY_NETWORK_WIZNET5K
    { MP_ROM_QSTR(MP_QSTR_WIZNET5K), MP_ROM_PTR(&mod_network_nic_type_wiznet5k) },
    #endif

    #if MICROPY_PY_NETWORK_NINAW10
    { MP_ROM_QSTR(MP_QSTR_WLAN), MP_ROM_PTR(&mod_network_nic_type_nina) },
    #endif

    #if MICROPY_PY_NETWORK_ESP_HOSTED
    { MP_ROM_QSTR(MP_QSTR_WLAN), MP_ROM_PTR(&mod_network_esp_hosted_type) },
    #endif

    // Allow a port to take mostly full control of the network module.
    #ifdef MICROPY_PY_NETWORK_MODULE_GLOBALS_INCLUDEFILE
    #include MICROPY_PY_NETWORK_MODULE_GLOBALS_INCLUDEFILE
    #else
    // Constants
    { MP_ROM_QSTR(MP_QSTR_STA_IF), MP_ROM_INT(MOD_NETWORK_STA_IF) },
    { MP_ROM_QSTR(MP_QSTR_AP_IF), MP_ROM_INT(MOD_NETWORK_AP_IF) },
    #endif
};

static MP_DEFINE_CONST_DICT(mp_module_network_globals, mp_module_network_globals_table);

const mp_obj_module_t mp_module_network = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mp_module_network_globals,
};

MP_REGISTER_MODULE(MP_QSTR_network, mp_module_network);

#endif  // MICROPY_PY_NETWORK
