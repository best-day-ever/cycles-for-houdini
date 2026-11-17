# Keep loader pointer tables private: OIDN's CUDA module must resolve cuInit
# to the driver function, rather than Cycles' CUEW variable of the same name.
# The source-pinned USD header also needs a C++20 constructor spelling overlay.
function(cycles_apply_native_linux_compat)
  foreach(cycles_loader extern_cuew extern_hipew)
    if(TARGET ${cycles_loader})
      set_target_properties(${cycles_loader} PROPERTIES C_VISIBILITY_PRESET hidden)
    endif()
  endforeach()
  if(TARGET hdCycles)
    # Upstream installs this target as a file, bypassing CMake's RPATH rewrite.
    set_target_properties(hdCycles PROPERTIES
      BUILD_WITH_INSTALL_RPATH TRUE INSTALL_RPATH "$ORIGIN/../../lib")
  endif()
  if(NOT CYCLES_USD_COMPAT_INCLUDE)
    return()
  endif()
  foreach(cycles_usd_target cycles_hydra hdCycles)
    if(TARGET ${cycles_usd_target})
      target_include_directories(${cycles_usd_target} BEFORE PRIVATE
        "${CYCLES_USD_COMPAT_INCLUDE}")
    endif()
  endforeach()
endfunction()

cmake_language(DEFER CALL cycles_apply_native_linux_compat)
