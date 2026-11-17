# The source-pinned USD 26.08 header contains a C++20-invalid constructor
# template-id. bootstrap.py creates a hash-guarded, ABI-identical header overlay.
# Apply it only to USD consumers, retaining cached CPU/GPU kernel objects.
function(cycles_apply_usd_cxx20_compat)
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

cmake_language(DEFER CALL cycles_apply_usd_cxx20_compat)
