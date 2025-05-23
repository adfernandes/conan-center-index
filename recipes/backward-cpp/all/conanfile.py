from conan import ConanFile
from conan.errors import ConanInvalidConfiguration
from conan.tools.build import check_min_cppstd
from conan.tools.cmake import CMake, CMakeDeps, CMakeToolchain, cmake_layout
from conan.tools.files import apply_conandata_patches, copy, export_conandata_patches, get, rmdir
from conan.tools.layout import basic_layout
from conan.tools.scm import Version
import os

required_conan_version = ">=2.1"


class BackwardCppConan(ConanFile):
    name = "backward-cpp"
    description = "A beautiful stack trace pretty printer for C++"
    homepage = "https://github.com/bombela/backward-cpp"
    url = "https://github.com/conan-io/conan-center-index"
    topics = ("backward-cpp", "stack-trace")
    license = "MIT"

    package_type = "library"
    settings = "os", "arch", "compiler", "build_type"
    options = {
        "header_only": [True, False],
        "shared": [True, False],
        "fPIC": [True, False],
        "stack_walking": ["unwind", "libunwind", "backtrace"],
        "stack_details": ["dw", "bfd", "backtrace_symbol", "dwarf"],
    }
    default_options = {
        "header_only": False,
        "shared": False,
        "fPIC": True,
        "stack_walking": "unwind",
        "stack_details": "dw",
    }

    @property
    def _supported_os(self):
        supported_os = ["Linux", "FreeBSD", "Android", "Macos"]
        if Version(self.version) >= "1.5":
            supported_os.append("Windows")
        return supported_os

    def _has_stack_walking(self, method):
        return self.options.stack_walking == method

    def _has_stack_details(self, method):
        return False if self.settings.os == "Windows" else self.options.stack_details == method

    def export_sources(self):
        export_conandata_patches(self)

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC
            del self.options.stack_details
        # default option
        if self.settings.os == "Macos":
            self.options.stack_details = "backtrace_symbol"

    def configure(self):
        if self.options.header_only:
            self.options.rm_safe("fPIC")
            self.options.rm_safe("shared")
        if self.options.get_safe("shared"):
            self.options.rm_safe("fPIC")

    def layout(self):
        if self.options.header_only:
            basic_layout(self, src_folder="src")
        else:
            cmake_layout(self, src_folder="src")

    def package_id(self):
        if self.info.options.header_only:
            self.info.clear()

    def requirements(self):
        if self.settings.os in ["Linux", "FreeBSD", "Android"]:
            if self._has_stack_walking("libunwind"):
                # transitive_headers: backward.hpp:301 Requires unwind.h
                self.requires("libunwind/1.7.2", transitive_headers=True)
            if self._has_stack_details("dw"):
                # transitive_headers: backward.hpp:215 Requires elfuleils/libdw.h elfutils/libdwfl.h and dwarf.h
                # transitive_libs: backward.hpp:1547 consumes dwarf_ranges() from libdw directly
                self.requires("elfutils/0.190", transitive_headers=True, transitive_libs=True)
            if self._has_stack_details("bfd"):
                # transitive_headers: backward.hpp:205 Requires bfd.h
                self.requires("binutils/2.42", transitive_headers=True)

    def validate(self):
        if self.settings.os not in self._supported_os:
            raise ConanInvalidConfiguration(f"{self.ref} is not supported on {self.settings.os}.")
        if self.settings.compiler.get_safe("cppstd"):
            check_min_cppstd(self, 11)
        if self._has_stack_walking("libunwind"):
            if Version(self.version) < "1.6":
                raise ConanInvalidConfiguration("Support for libunwind is only available as of 1.6.")
            if self.settings.os == "Windows":
                raise ConanInvalidConfiguration("Support for libunwind is only available on Linux and macOS.")
        if self.settings.os == "Macos":
            if self.settings.arch == "armv8" and Version(self.version) < "1.6":
                raise ConanInvalidConfiguration("Support for Apple Silicon is only available as of 1.6.")
            if not self._has_stack_details("backtrace_symbol"):
                raise ConanInvalidConfiguration("Stack details other than backtrace_symbol are not supported on macOS.")
        if self._has_stack_details("dwarf"):
            # INFO: backward-cpp consumes an old version of libdwarf which is API incompatible.
            # See https://github.com/bombela/backward-cpp/issues/232
            raise ConanInvalidConfiguration("Stack details dwarf requires libdwarf <20191104 which is not available in Conan Center Index.")

    def source(self):
        get(self, **self.conan_data["sources"][self.version], strip_root=True)

    def generate(self):
        if self.options.header_only:
            return
        tc = CMakeToolchain(self)
        tc.cache_variables["STACK_WALKING_UNWIND"] = self._has_stack_walking("unwind")
        tc.cache_variables["STACK_WALKING_LIBUNWIND"] = self._has_stack_walking("libunwind")
        tc.cache_variables["STACK_WALKING_BACKTRACE"] = self._has_stack_walking("backtrace")
        tc.cache_variables["STACK_DETAILS_AUTO_DETECT"] = False
        tc.cache_variables["STACK_DETAILS_BACKTRACE_SYMBOL"] = self._has_stack_details("backtrace_symbol")
        tc.cache_variables["STACK_DETAILS_DW"] = self._has_stack_details("dw")
        tc.cache_variables["STACK_DETAILS_BFD"] = self._has_stack_details("bfd")
        tc.cache_variables["STACK_DETAILS_DWARF"] = False
        tc.cache_variables["BACKWARD_SHARED"] = self.options.get_safe("shared", False)
        tc.cache_variables["BACKWARD_TESTS"] = False
        tc.cache_variables["CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS"] = True
        tc.generate()
        deps = CMakeDeps(self)
        deps.generate()

    def build(self):
        apply_conandata_patches(self)
        if self.options.header_only:
            return
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        copy(self, "LICENSE*", src=self.source_folder, dst=os.path.join(self.package_folder, "licenses"))
        if self.options.header_only:
            copy(self, pattern="*.hpp",
                 src=self.source_folder,
                 dst=os.path.join(self.package_folder, "include"))
        else:
            cmake = CMake(self)
            cmake.install()
            rmdir(self, os.path.join(self.package_folder, "lib", "backward"))

    def package_info(self):
        self.cpp_info.set_property("cmake_file_name", "Backward")
        self.cpp_info.set_property("cmake_target_name", "Backward::Backward")

        self.cpp_info.defines.append(f"BACKWARD_HAS_UNWIND={int(self._has_stack_walking('unwind'))}")
        self.cpp_info.defines.append(f"BACKWARD_HAS_LIBUNWIND={int(self._has_stack_walking('libunwind'))}")
        self.cpp_info.defines.append(f"BACKWARD_HAS_BACKTRACE={int(self._has_stack_walking('backtrace'))}")

        self.cpp_info.defines.append(f"BACKWARD_HAS_BACKTRACE_SYMBOL={int(self._has_stack_details('backtrace_symbol'))}")
        self.cpp_info.defines.append(f"BACKWARD_HAS_DW={int(self._has_stack_details('dw'))}")
        self.cpp_info.defines.append(f"BACKWARD_HAS_BFD={int(self._has_stack_details('bfd'))}")
        self.cpp_info.defines.append(f"BACKWARD_HAS_DWARF=0")
        self.cpp_info.defines.append(f"BACKWARD_HAS_PDB_SYMBOL={int(self.settings.os == 'Windows')}")

        if self.options.header_only:
            self.cpp_info.libdirs = []
            self.cpp_info.bindirs = []
        else:
            self.cpp_info.libs = ["backward"]
        if self.settings.os in ["Linux", "FreeBSD", "Android"]:
            self.cpp_info.system_libs.extend(["dl", "m"])
        if self.settings.os == "Windows":
            self.cpp_info.system_libs.extend(["psapi", "dbghelp"])
