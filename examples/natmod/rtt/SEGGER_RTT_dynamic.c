/*********************************************************************
*                    SEGGER Microcontroller GmbH                     *
*                        The Embedded Experts                        *
**********************************************************************
*                                                                    *
*            (c) 1995 - 2021 SEGGER Microcontroller GmbH             *
*                                                                    *
*       www.segger.com     Support: support@segger.com               *
*                                                                    *
**********************************************************************
*                                                                    *
* All rights reserved.                                               *
*                                                                    *
* Redistribution and use in source and binary forms, with or        *
* without modification, are permitted provided that the following    *
* conditions are met:                                                *
*                                                                    *
* - Redistributions of source code must retain the above copyright  *
*   notice, this condition and the following disclaimer.            *
*                                                                    *
* THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND            *
* CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,       *
* INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF          *
* MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE          *
* DISCLAIMED. IN NO EVENT SHALL SEGGER Microcontroller BE LIABLE FOR*
* ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR          *
* CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT *
* OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;   *
* OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF     *
* LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT         *
* (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE *
* USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH  *
* DAMAGE.                                                            *
*                                                                    *
**********************************************************************
---------------------------END-OF-HEADER------------------------------

File    : SEGGER_RTT_patched.c
Purpose : SEGGER RTT implementation patched for MicroPython native modules.
Revision: Based on SEGGER RTT original version with modifications for
          dynamic allocation compatibility.

=== MODIFICATIONS FOR MICROPYTHON NATIVE MODULES ===
This is a modified version of SEGGER_RTT.c with the following changes:

1. Replaced global static _SEGGER_RTT control block with dynamically allocated pointer
2. Added RTT_GetControlBlock() function to access the control block pointer
3. Added RTT_InitControlBlock() for dynamic initialization
4. Added RTT_FreeControlBlock() for cleanup
5. Modified all references to use the pointer instead of global variable
6. Added proper initialization checks throughout
7. Replaced memcpy with simple copying to avoid external dependencies

Original source: https://github.com/SEGGERMicro/RTT/tree/main/RTT
These modifications allow RTT to work with MicroPython's native module
system which doesn't support global BSS variables.

=== END MODIFICATIONS ===
----------------------------------------------------------------------
*/

#include "SEGGER_RTT_dynamic.h"

/*********************************************************************
*
*       Configuration, default values - MODIFIED FOR MICROPYTHON
*
**********************************************************************
*/

// Global control block pointer - initialized to NULL
SEGGER_RTT_CB* _SEGGER_RTT_PTR = ((SEGGER_RTT_CB*)0);

// Forward declarations
static unsigned _SEGGER_RTT_WriteNoLock(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes);
static unsigned _SEGGER_RTT_ReadNoLock(unsigned BufferIndex, void* pBuffer, unsigned BufferSize);

/*********************************************************************
*
*       Control block management functions - NEW FOR MICROPYTHON
*
**********************************************************************
*/

/*********************************************************************
*
*       RTT_GetControlBlock
*
*  Function description
*    Returns the RTT control block pointer, allocating if necessary.
*
*  Return value
*    Pointer to RTT control block, or NULL on allocation failure.
*/
SEGGER_RTT_CB* RTT_GetControlBlock(void) {
    if (_SEGGER_RTT_PTR == ((SEGGER_RTT_CB*)0)) {
        RTT_InitControlBlock();
    }
    return _SEGGER_RTT_PTR;
}

/*********************************************************************
*
*       RTT_InitControlBlock
*
*  Function description
*    Initializes the RTT control block with dynamic allocation.
*
*  Return value
*    0: Success
*   -1: Allocation failed
*/
int RTT_InitControlBlock(void) {
    SEGGER_RTT_CB* pCB;
    unsigned i;

    if (_SEGGER_RTT_PTR != ((SEGGER_RTT_CB*)0)) {
        return 0; // Already initialized
    }

    // Use global variables defined in rtt.c to avoid BSS issues
    extern SEGGER_RTT_CB _rtt_cb_global;
    extern char _rtt_up_buffer_global[BUFFER_SIZE_UP];
    extern char _rtt_down_buffer_global[BUFFER_SIZE_DOWN];

    pCB = &_rtt_cb_global;
    _SEGGER_RTT_PTR = pCB;

    // Initialize ID string
    pCB->acID[0] = 'S';
    pCB->acID[1] = 'E';
    pCB->acID[2] = 'G';
    pCB->acID[3] = 'G';
    pCB->acID[4] = 'E';
    pCB->acID[5] = 'R';
    pCB->acID[6] = ' ';
    pCB->acID[7] = 'R';
    pCB->acID[8] = 'T';
    pCB->acID[9] = 'T';
    pCB->acID[10] = '\0';
    for (i = 11; i < 16; i++) {
        pCB->acID[i] = '\0';
    }

    // Set buffer counts
    pCB->MaxNumUpBuffers = SEGGER_RTT_MAX_NUM_UP_BUFFERS;
    pCB->MaxNumDownBuffers = SEGGER_RTT_MAX_NUM_DOWN_BUFFERS;

    // Initialize all buffers to empty
    for (i = 0; i < SEGGER_RTT_MAX_NUM_UP_BUFFERS; i++) {
        pCB->aUp[i].sName = ((const char*)0);
        pCB->aUp[i].pBuffer = ((char*)0);
        pCB->aUp[i].SizeOfBuffer = 0u;
        pCB->aUp[i].WrOff = 0u;
        pCB->aUp[i].RdOff = 0u;
        pCB->aUp[i].Flags = 0u;
    }

    for (i = 0; i < SEGGER_RTT_MAX_NUM_DOWN_BUFFERS; i++) {
        pCB->aDown[i].sName = ((const char*)0);
        pCB->aDown[i].pBuffer = ((char*)0);
        pCB->aDown[i].SizeOfBuffer = 0u;
        pCB->aDown[i].WrOff = 0u;
        pCB->aDown[i].RdOff = 0u;
        pCB->aDown[i].Flags = 0u;
    }

    // Set up default terminal buffer (channel 0)
    pCB->aUp[0].sName = "Terminal";
    pCB->aUp[0].pBuffer = _rtt_up_buffer_global;
    pCB->aUp[0].SizeOfBuffer = BUFFER_SIZE_UP;
    pCB->aUp[0].RdOff = 0u;
    pCB->aUp[0].WrOff = 0u;
    pCB->aUp[0].Flags = SEGGER_RTT_MODE_DEFAULT;

    pCB->aDown[0].sName = "Terminal";
    pCB->aDown[0].pBuffer = _rtt_down_buffer_global;
    pCB->aDown[0].SizeOfBuffer = BUFFER_SIZE_DOWN;
    pCB->aDown[0].RdOff = 0u;
    pCB->aDown[0].WrOff = 0u;
    pCB->aDown[0].Flags = SEGGER_RTT_MODE_DEFAULT;

    return 0;
}

/*********************************************************************
*
*       RTT_FreeControlBlock
*
*  Function description
*    Frees the RTT control block.
*/
void RTT_FreeControlBlock(void) {
    // For static allocation approach, just reset pointer
    _SEGGER_RTT_PTR = ((SEGGER_RTT_CB*)0);
}

/*********************************************************************
*
*       Static functions - MODIFIED TO USE POINTER
*
**********************************************************************
*/

/*********************************************************************
*
*       _SEGGER_RTT_WriteNoLock
*
*  Function description
*    Stores a specified number of characters in SEGGER RTT ring buffer
*    and updates the associated write pointer which is periodically
*    read by the host.
*    SEGGER_RTT_WriteNoLock does not lock the application and add the
*    data into the buffer.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to character array. Does not need to point to a \0 terminated string.
*    NumBytes     Number of bytes to be stored in the SEGGER RTT control block.
*
*  Return value
*    Number of bytes which have been stored in the "Up"-buffer.
*
*  Notes
*    (1) If there is not enough space in the "Up"-buffer, not all bytes are copied.
*    (2) For performance reasons this function does not call Init()
*        and may only be called after RTT has been initialized.
*        Either by calling SEGGER_RTT_Init() or by calling another RTT API function first.
*/
static unsigned _SEGGER_RTT_WriteNoLock(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes) {
    unsigned NumBytesToWrite;
    unsigned NumBytesWritten;
    unsigned RdOff;
    unsigned WrOff;
    const char* pData;
    SEGGER_RTT_BUFFER_UP* pRing;
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    pData = (const char*)pBuffer;
    pRing = &pCB->aUp[BufferIndex];
    NumBytesToWrite = NumBytes;
    NumBytesWritten = 0u;

    RdOff = pRing->RdOff;
    WrOff = pRing->WrOff;

    if (RdOff > WrOff) {
        NumBytesToWrite = RdOff - WrOff - 1u;
        if (NumBytesToWrite > NumBytes) {
            NumBytesToWrite = NumBytes;
        }
        NumBytesWritten = NumBytesToWrite;
        // Simple byte copy
        while (NumBytesToWrite > 0u) {
            pRing->pBuffer[WrOff] = *pData++;
            WrOff++;
            NumBytesToWrite--;
        }
        pRing->WrOff = WrOff;
    } else if ((RdOff == 0u) && (WrOff == (pRing->SizeOfBuffer - 1u))) {
        // Buffer full
        NumBytesWritten = 0u;
    } else {
        NumBytesToWrite = pRing->SizeOfBuffer - 1u - WrOff;
        if (NumBytesToWrite > NumBytes) {
            NumBytesToWrite = NumBytes;
        }
        NumBytesWritten = NumBytesToWrite;
        // Simple byte copy to end
        while (NumBytesToWrite > 0u) {
            pRing->pBuffer[WrOff] = *pData++;
            WrOff++;
            NumBytesToWrite--;
        }
        NumBytes -= NumBytesWritten;

        if ((NumBytes > 0u) && (RdOff > 1u)) {
            NumBytesToWrite = RdOff - 1u;
            if (NumBytesToWrite > NumBytes) {
                NumBytesToWrite = NumBytes;
            }
            NumBytesWritten += NumBytesToWrite;
            WrOff = 0u;
            // Simple byte copy from beginning
            while (NumBytesToWrite > 0u) {
                pRing->pBuffer[WrOff] = *pData++;
                WrOff++;
                NumBytesToWrite--;
            }
        }
        pRing->WrOff = WrOff;
    }

    return NumBytesWritten;
}

/*********************************************************************
*
*       _SEGGER_RTT_ReadNoLock
*
*  Function description
*    Reads a specified number of characters from SEGGER RTT ring buffer.
*    SEGGER_RTT_ReadNoLock does not lock the application.
*
*  Parameters
*    BufferIndex  Index of "Down"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to buffer provided by target application, to copy data into.
*    BufferSize   Size of the buffer provided by target application.
*
*  Return value
*    Number of bytes that have been read.
*/
static unsigned _SEGGER_RTT_ReadNoLock(unsigned BufferIndex, void* pBuffer, unsigned BufferSize) {
    unsigned NumBytesRem;
    unsigned NumBytesRead;
    unsigned RdOff;
    unsigned WrOff;
    unsigned char* pData;
    SEGGER_RTT_BUFFER_DOWN* pRing;
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    pData = (unsigned char*)pBuffer;
    pRing = &pCB->aDown[BufferIndex];
    NumBytesRead = 0u;
    RdOff = pRing->RdOff;
    WrOff = pRing->WrOff;

    if (RdOff > WrOff) {
        NumBytesRem = pRing->SizeOfBuffer - RdOff;
        NumBytesRem = (NumBytesRem < BufferSize) ? NumBytesRem : BufferSize;
        // Simple byte copy
        while (NumBytesRem > 0u) {
            *pData++ = pRing->pBuffer[RdOff];
            RdOff++;
            NumBytesRem--;
        }
        NumBytesRead = pRing->SizeOfBuffer - pRing->RdOff;
        BufferSize -= NumBytesRead;

        if ((BufferSize > 0u) && (WrOff > 0u)) {
            NumBytesRem = (WrOff < BufferSize) ? WrOff : BufferSize;
            RdOff = 0u;
            // Simple byte copy from beginning
            while (NumBytesRem > 0u) {
                *pData++ = pRing->pBuffer[RdOff];
                RdOff++;
                NumBytesRem--;
            }
            NumBytesRead += pRing->RdOff;
        }
        pRing->RdOff = RdOff;
    } else {
        NumBytesRem = WrOff - RdOff;
        NumBytesRem = (NumBytesRem < BufferSize) ? NumBytesRem : BufferSize;
        NumBytesRead = NumBytesRem;
        // Simple byte copy
        while (NumBytesRem > 0u) {
            *pData++ = pRing->pBuffer[RdOff];
            RdOff++;
            NumBytesRem--;
        }
        pRing->RdOff = RdOff;
    }

    return NumBytesRead;
}

/*********************************************************************
*
*       Public API functions - MODIFIED TO USE POINTER
*
**********************************************************************
*/

/*********************************************************************
*
*       SEGGER_RTT_Init
*
*  Function description
*    Initializes the SEGGER RTT control block and sets up the up buffer.
*/
void SEGGER_RTT_Init(void) {
    RTT_InitControlBlock();
}

/*********************************************************************
*
*       SEGGER_RTT_Write
*
*  Function description
*    Stores a specified number of characters in SEGGER RTT ring buffer
*    and updates the associated write pointer which is periodically
*    read by the host.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to character array. Does not need to point to a \0 terminated string.
*    NumBytes     Number of bytes to be stored in the SEGGER RTT control block.
*
*  Return value
*    Number of bytes which have been stored in the "Up"-buffer.
*
*  Notes
*    (1) If there is not enough space in the "Up"-buffer, not all bytes are copied.
*/
unsigned SEGGER_RTT_Write(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes) {
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();
    unsigned Result;

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    Result = _SEGGER_RTT_WriteNoLock(BufferIndex, pBuffer, NumBytes);

    return Result;
}

/*********************************************************************
*
*       SEGGER_RTT_WriteNoLock
*
*  Function description
*    Stores a specified number of characters in SEGGER RTT ring buffer
*    and updates the associated write pointer which is periodically
*    read by the host.
*    SEGGER_RTT_WriteNoLock does not lock the application and add the
*    data into the buffer.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to character array. Does not need to point to a \0 terminated string.
*    NumBytes     Number of bytes to be stored in the SEGGER RTT control block.
*
*  Return value
*    Number of bytes which have been stored in the "Up"-buffer.
*
*  Notes
*    (1) If there is not enough space in the "Up"-buffer, not all bytes are copied.
*    (2) For performance reasons this function does not call Init()
*        and may only be called after RTT has been initialized.
*        Either by calling SEGGER_RTT_Init() or by calling another RTT API function first.
*/
unsigned SEGGER_RTT_WriteNoLock(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes) {
    return _SEGGER_RTT_WriteNoLock(BufferIndex, pBuffer, NumBytes);
}

/*********************************************************************
*
*       SEGGER_RTT_Read
*
*  Function description
*    Reads a specified number of characters from SEGGER RTT ring buffer.
*
*  Parameters
*    BufferIndex  Index of "Down"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to buffer provided by target application, to copy data into.
*    BufferSize   Size of the buffer provided by target application.
*
*  Return value
*    Number of bytes that have been read.
*/
unsigned SEGGER_RTT_Read(unsigned BufferIndex, void* pBuffer, unsigned BufferSize) {
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();
    unsigned Result;

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    Result = _SEGGER_RTT_ReadNoLock(BufferIndex, pBuffer, BufferSize);

    return Result;
}

/*********************************************************************
*
*       SEGGER_RTT_ReadNoLock
*
*  Function description
*    Reads a specified number of characters from SEGGER RTT ring buffer.
*    SEGGER_RTT_ReadNoLock does not lock the application.
*
*  Parameters
*    BufferIndex  Index of "Down"-buffer to be used (e.g. 0 for "Terminal").
*    pBuffer      Pointer to buffer provided by target application, to copy data into.
*    BufferSize   Size of the buffer provided by target application.
*
*  Return value
*    Number of bytes that have been read.
*/
unsigned SEGGER_RTT_ReadNoLock(unsigned BufferIndex, void* pBuffer, unsigned BufferSize) {
    return _SEGGER_RTT_ReadNoLock(BufferIndex, pBuffer, BufferSize);
}

/*********************************************************************
*
*       SEGGER_RTT_HasData
*
*  Function description
*    Check if there is data from the host in the given buffer.
*
*  Parameters
*    BufferIndex  Index of "Down"-buffer to be checked (e.g. 0 for "Terminal").
*
*  Return value
*    >0: Number of bytes available in buffer.
*     0: No data available in buffer.
*/
unsigned SEGGER_RTT_HasData(unsigned BufferIndex) {
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();
    SEGGER_RTT_BUFFER_DOWN* pRing;
    unsigned RdOff;
    unsigned WrOff;
    unsigned Result;

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    pRing = &pCB->aDown[BufferIndex];
    RdOff = pRing->RdOff;
    WrOff = pRing->WrOff;

    if (RdOff <= WrOff) {
        Result = WrOff - RdOff;
    } else {
        Result = pRing->SizeOfBuffer - RdOff + WrOff;
    }

    return Result;
}

/*********************************************************************
*
*       SEGGER_RTT_GetAvailWriteSpace
*
*  Function description
*    Get available space in buffer.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be checked (e.g. 0 for "Terminal").
*
*  Return value
*    Number of bytes available in buffer.
*/
unsigned SEGGER_RTT_GetAvailWriteSpace(unsigned BufferIndex) {
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();
    SEGGER_RTT_BUFFER_UP* pRing;
    unsigned RdOff;
    unsigned WrOff;
    unsigned Result;

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0u;
    }

    pRing = &pCB->aUp[BufferIndex];
    RdOff = pRing->RdOff;
    WrOff = pRing->WrOff;

    if (RdOff <= WrOff) {
        Result = pRing->SizeOfBuffer - 1u - WrOff + RdOff;
    } else {
        Result = RdOff - WrOff - 1u;
    }

    return Result;
}

/*********************************************************************
*
*       SEGGER_RTT_WriteString
*
*  Function description
*    Stores string in SEGGER RTT control block.
*    This data is read by the host.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be used (e.g. 0 for "Terminal").
*    s            Pointer to string.
*
*  Return value
*    Number of bytes which have been stored in the "Up"-buffer.
*
*  Notes
*    (1) If there is not enough space in the "Up"-buffer, not all bytes are copied.
*/
unsigned SEGGER_RTT_WriteString(unsigned BufferIndex, const char* s) {
    // Simple strlen implementation
    unsigned Len = 0u;
    const char* p = s;
    while (*p) {
        p++;
        Len++;
    }

    return SEGGER_RTT_Write(BufferIndex, s, Len);
}

/*********************************************************************
*
*       SEGGER_RTT_PutChar
*
*  Function description
*    Stores a single character/byte in SEGGER RTT ring buffer.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be used (e.g. 0 for "Terminal").
*    c            Byte to be stored.
*
*  Return value
*    Number of bytes which have been stored in the "Up"-buffer.
*
*  Notes
*    (1) If there is not enough space in the "Up"-buffer, the character is dropped.
*/
unsigned SEGGER_RTT_PutChar(unsigned BufferIndex, char c) {
    return SEGGER_RTT_Write(BufferIndex, &c, 1u);
}

/*********************************************************************
*
*       SEGGER_RTT_GetKey
*
*  Function description
*    Reads one character from the SEGGER RTT buffer.
*    Host has previously stored data there.
*
*  Return value
*    <0: No character available (buffer empty).
*   >=0: Character which has been read. (Possible values: 0 - 255)
*
*  Notes
*    (1) This function is only specified for accesses to RTT buffer 0.
*/
int SEGGER_RTT_GetKey(void) {
    char c;
    int r;

    r = (int)SEGGER_RTT_Read(0u, &c, 1u);
    if (r == 1) {
        r = (int)(unsigned char)c;
    } else {
        r = -1;
    }

    return r;
}

/*********************************************************************
*
*       SEGGER_RTT_WaitForInput
*
*  Function description
*    Waits until at least one character is available in the SEGGER RTT buffer.
*    Once a character is available, this character is read and this
*    function returns.
*
*  Return value
*    >=0: Character which has been read.
*
*  Notes
*    (1) This function is only specified for accesses to RTT buffer 0
*    (2) This function NEVER returns a negative value.
*/
int SEGGER_RTT_WaitForInput(void) {
    int r;

    do {
        r = SEGGER_RTT_GetKey();
    } while (r < 0);

    return r;
}

/*********************************************************************
*
*       SEGGER_RTT_HasDataUp
*
*  Function description
*    Check if there is data remaining to be sent in the given buffer.
*
*  Parameters
*    BufferIndex  Index of "Up"-buffer to be checked (e.g. 0 for "Terminal").
*
*  Return value
*    == 0: No data
*    != 0: Data in buffer
*/
int SEGGER_RTT_HasDataUp(unsigned BufferIndex) {
    SEGGER_RTT_CB* pCB = RTT_GetControlBlock();
    SEGGER_RTT_BUFFER_UP* pRing;

    if (pCB == ((SEGGER_RTT_CB*)0)) {
        return 0;
    }

    pRing = &pCB->aUp[BufferIndex];
    return (pRing->WrOff != pRing->RdOff) ? 1 : 0;
}

// Stub implementations for functions we don't fully support yet
int SEGGER_RTT_AllocDownBuffer(const char* sName, void* pBuffer, unsigned BufferSize, unsigned Flags) {
    (void)sName; (void)pBuffer; (void)BufferSize; (void)Flags;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_AllocUpBuffer(const char* sName, void* pBuffer, unsigned BufferSize, unsigned Flags) {
    (void)sName; (void)pBuffer; (void)BufferSize; (void)Flags;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_ConfigUpBuffer(unsigned BufferIndex, const char* sName, void* pBuffer, unsigned BufferSize, unsigned Flags) {
    (void)BufferIndex; (void)sName; (void)pBuffer; (void)BufferSize; (void)Flags;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_ConfigDownBuffer(unsigned BufferIndex, const char* sName, void* pBuffer, unsigned BufferSize, unsigned Flags) {
    (void)BufferIndex; (void)sName; (void)pBuffer; (void)BufferSize; (void)Flags;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_SetNameDownBuffer(unsigned BufferIndex, const char* sName) {
    (void)BufferIndex; (void)sName;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_SetNameUpBuffer(unsigned BufferIndex, const char* sName) {
    (void)BufferIndex; (void)sName;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_SetFlagsDownBuffer(unsigned BufferIndex, unsigned Flags) {
    (void)BufferIndex; (void)Flags;
    return -1; // Not supported in this simplified version
}

int SEGGER_RTT_SetFlagsUpBuffer(unsigned BufferIndex, unsigned Flags) {
    (void)BufferIndex; (void)Flags;
    return -1; // Not supported in this simplified version
}

unsigned SEGGER_RTT_WriteSkipNoLock(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes) {
    return SEGGER_RTT_WriteNoLock(BufferIndex, pBuffer, NumBytes);
}

void SEGGER_RTT_WriteWithOverwriteNoLock(unsigned BufferIndex, const void* pBuffer, unsigned NumBytes) {
    (void)SEGGER_RTT_WriteNoLock(BufferIndex, pBuffer, NumBytes);
}

unsigned SEGGER_RTT_PutCharSkip(unsigned BufferIndex, char c) {
    return SEGGER_RTT_PutChar(BufferIndex, c);
}

unsigned SEGGER_RTT_PutCharSkipNoLock(unsigned BufferIndex, char c) {
    return SEGGER_RTT_WriteNoLock(BufferIndex, &c, 1u);
}

unsigned SEGGER_RTT_GetBytesInBuffer(unsigned BufferIndex) {
    return SEGGER_RTT_HasData(BufferIndex);
}

/*************************** End of file ****************************/