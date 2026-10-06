/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_CONFIG_MCUBOOT_LOGGING_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_CONFIG_MCUBOOT_LOGGING_H

#include "bootutil/ignore.h"
#include "mcuboot_log.h"

#define MCUBOOT_LOG_MODULE_DECLARE(domain)
#define MCUBOOT_LOG_MODULE_REGISTER(domain)

// Each message is prefixed with the function name and terminated with a newline.
#if MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_ERROR
#define MCUBOOT_LOG_ERR(fmt, ...) mcuboot_log(MCUBOOT_LOG_LEVEL_ERROR, "%s: " fmt "\n", __func__,##__VA_ARGS__)
#else
#define MCUBOOT_LOG_ERR(...) IGNORE(__VA_ARGS__)
#endif

#if MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_WARNING
#define MCUBOOT_LOG_WRN(fmt, ...) mcuboot_log(MCUBOOT_LOG_LEVEL_WARNING, "%s: " fmt "\n", __func__,##__VA_ARGS__)
#else
#define MCUBOOT_LOG_WRN(...) IGNORE(__VA_ARGS__)
#endif

#if MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_INFO
#define MCUBOOT_LOG_INF(fmt, ...) mcuboot_log(MCUBOOT_LOG_LEVEL_INFO, "%s: " fmt "\n", __func__,##__VA_ARGS__)
#else
#define MCUBOOT_LOG_INF(...) IGNORE(__VA_ARGS__)
#endif

#if MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_DEBUG
#define MCUBOOT_LOG_DBG(fmt, ...) mcuboot_log(MCUBOOT_LOG_LEVEL_DEBUG, "%s: " fmt "\n", __func__,##__VA_ARGS__)
#else
#define MCUBOOT_LOG_DBG(...) IGNORE(__VA_ARGS__)
#endif

#define MCUBOOT_LOG_SIM(...) IGNORE(__VA_ARGS__)

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_CONFIG_MCUBOOT_LOGGING_H
