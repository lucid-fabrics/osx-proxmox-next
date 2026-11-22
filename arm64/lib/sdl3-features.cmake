# CMake cache presets for the SDL3 that reims-vgpu builds from source (sdl3-sys, video subsystem only), loaded through
# CMAKE_TOOLCHAIN_FILE by `snapintosh build` (R13).
#
# SDL3's cmake switches optional parts on when their development packages are installed (D-Bus, IBus, libudev, liburing, PipeWire,
# Wayland and libdecor, KMS/DRM, FriBidi, libthai, OpenGL), so libreims_vgpu.a depended on the host. The lab never opens a window
# (REIMS_VGPU_WINDOW=0, QEMU runs -display none); what stays is the X11 window path (Vulkan surface) with its extensions, which
# `build --deps` installs, so a missing package fails the build instead of silently dropping a feature.
foreach(opt DBUS IBUS FRIBIDI LIBTHAI LIBUDEV LIBURING PIPEWIRE WAYLAND KMSDRM OPENGL OPENGLES OPENVR)
  set(SDL_${opt} OFF CACHE BOOL "R13 fixed feature set" FORCE)
endforeach()
foreach(opt X11 X11_XCURSOR X11_XDBE X11_XFIXES X11_XINPUT X11_XRANDR X11_XSCRNSAVER X11_XSHAPE X11_XSYNC X11_XTEST VULKAN)
  set(SDL_${opt} ON CACHE BOOL "R13 fixed feature set" FORCE)
endforeach()
