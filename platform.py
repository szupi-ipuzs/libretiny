from os.path import dirname

from platformio.debug.config.base import DebugConfigBase
from platformio.debug.exception import DebugInvalidOptionsError
from platformio.managers.platform import PlatformBase
from platformio.package.exception import MissingPackageManifestError
from platformio.package.manager.base import BasePackageManager
from platformio.package.meta import PackageItem, PackageSpec
from platformio.platform.board import PlatformBoardConfig

libretuya_packages = None
manifest_default = {"version": "0.0.0", "description": "", "keywords": []}


def load_manifest(self, src):
    try:
        return BasePackageManager._load_manifest(self, src)
    except MissingPackageManifestError:
        # ignore all exceptions
        pass
    # get the installation temporary path
    path = src.path if isinstance(src, PackageItem) else src
    # raise the exception if this package is not from libretuya
    if (
        not hasattr(self, "spec_map")
        or path not in self.spec_map
        or not libretuya_packages
    ):
        raise MissingPackageManifestError(", ".join(self.manifest_names))
    # get the saved spec
    spec: PackageSpec = self.spec_map[path]
    # read package data from platform.json
    manifest: dict = libretuya_packages[spec.name]
    # find additional manifest info
    manifest = manifest.get("manifest", manifest_default)
    # put info from spec
    manifest.update(
        {
            "name": spec.name,
            "repository": {
                "type": "git",
                "url": spec.url,
            },
        }
    )
    # save in cache
    cache_key = "load_manifest-%s" % path
    self.memcache_set(cache_key, manifest)
    # result = ManifestParserFactory.new(json.dumps(manifest), ManifestFileType.PACKAGE_JSON).as_dict()
    return manifest


def find_pkg_root(self, path: str, spec: PackageSpec):
    try:
        return BasePackageManager._find_pkg_root(self, path, spec)
    except MissingPackageManifestError as e:
        # raise the exception if this package is not from libretuya
        if not libretuya_packages or spec.name not in libretuya_packages:
            raise e
    # save the spec for later
    if not hasattr(self, "spec_map"):
        self.spec_map = {}
    self.spec_map[path] = spec
    return path


class LibretuyaPlatform(PlatformBase):
    def configure_default_packages(self, options, targets):
        framework = options.get("pioframework")[0]
        # patch find_pkg root to ignore missing manifests and save PackageSpec
        if not hasattr(BasePackageManager, "_find_pkg_root"):
            BasePackageManager._find_pkg_root = BasePackageManager.find_pkg_root
            BasePackageManager.find_pkg_root = find_pkg_root
        # patch load_manifest to generate manifests from PackageSpec
        if not hasattr(BasePackageManager, "_load_manifest"):
            BasePackageManager._load_manifest = BasePackageManager.load_manifest
            BasePackageManager.load_manifest = load_manifest

        # set specific compiler versions
        if framework.startswith("realtek-ambz"):
            self.packages["toolchain-gccarmnoneeabi"]["version"] = "~1.50401.0"

        # make ArduinoCore-API required
        if "arduino" in framework:
            self.packages["framework-arduino-api"]["optional"] = False

        # save platform packages for later
        global libretuya_packages
        libretuya_packages = self.packages

        return super().configure_default_packages(options, targets)

    def get_boards(self, id_=None):
        result = PlatformBase.get_boards(self, id_)
        if not result:
            return result
        if id_:
            return self._add_default_debug_tools(result)
        else:
            for key, value in result.items():
                result[key] = self._add_default_debug_tools(value)
        return result

    def _add_default_debug_tools(self, board: PlatformBoardConfig):
        # inspired by platform-ststm32/platform.py
        debug = board.manifest.get("debug", {})
        if not debug:
            return board
        protocols = debug.get("protocols", [])
        if "tools" not in debug:
            debug["tools"] = {}
        if "custom" not in debug["tools"]:
            debug["tools"]["custom"] = {}
        init = debug.get("gdb_init", [])
        init += ["set mem inaccessible-by-default off"]

        for link in protocols:
            if link == "openocd":
                args = ["-s", "$PACKAGE_DIR/scripts"]
                if debug.get("openocd_config"):
                    args.extend(
                        [
                            "-f",
                            "$LTPATH/platform/$LTPLATFORM/openocd/%s"
                            % debug.get("openocd_config"),
                        ]
                    )
                debug["tools"][link] = {
                    "server": {
                        "package": "tool-openocd",
                        "executable": "bin/openocd",
                        "arguments": args,
                    },
                    "extra_cmds": init,
                }
            else:
                continue
            debug["tools"][link]["default"] = link == debug.get("protocol", "")

        debug["tools"]["custom"]["extra_cmds"] = init

        board.manifest["debug"] = debug
        return board

    def configure_debug_session(self, debug_config: DebugConfigBase):
        opts = debug_config.env_options
        server = debug_config.server
        lt_path = dirname(__file__)
        lt_platform = opts["framework"][0].rpartition("-")[0]
        if not server:
            debug_tool = opts.get("debug_tool", "custom")
            board = opts.get("board", "<unknown>")
            if debug_tool == "custom":
                return
            exc = DebugInvalidOptionsError(
                f"[LibreTuya] Debug tool {debug_tool} is not supported by board {board}."
            )
            exc.MESSAGE = ""
            raise exc
        if "arguments" in server:
            # allow setting interface via platformio.ini
            if opts.get("openocd_interface"):
                server["arguments"] = [
                    "-f",
                    "interface/%s.cfg" % opts.get("openocd_interface"),
                ] + server["arguments"]
            # replace $LTPLATFORM with actual name
            server["arguments"] = [
                arg.replace("$LTPLATFORM", lt_platform).replace("$LTPATH", lt_path)
                for arg in server["arguments"]
            ]
