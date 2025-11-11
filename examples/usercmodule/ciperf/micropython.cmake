# Create an INTERFACE library for our C module
add_library(usermod_ciperf INTERFACE)

# Add source file
target_sources(usermod_ciperf INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/ciperf.c
)

# Add include directory
target_include_directories(usermod_ciperf INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

# Add optimization flags for maximum performance
target_compile_options(usermod_ciperf INTERFACE
    -O3
    -funroll-loops
    -finline-functions
)

# Link with usermod target
target_link_libraries(usermod INTERFACE usermod_ciperf)
