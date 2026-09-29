"""把整份文件 Lua 沙箱解析结果（resolve_full_modinfo()）应用到一个已经静态解析过的
ModInfo 上——批量重载（Mod 管理页加载）和单个 mod 兜底（配置弹窗）共用同一份规则，
确保两条路径产出完全一致的字段。"""


def apply_full_sandbox_result(mod_info, result: dict | None) -> None:
    if not result:
        mod_info.version_status = "unresolved"
        mod_info.version_source = ""
        mod_info.version_compatible_status = "unresolved"
        mod_info.full_sandbox_tried = True
        return
    if "config_options" in result:
        mod_info.config_options = result["config_options"]
        mod_info.unsupported_schema = False
    for key in ("name", "author", "version", "version_compatible", "description", "icon", "icon_atlas"):
        if key in result:
            setattr(mod_info, key, result[key])
    if "version" not in result or result["version"] is None:
        mod_info.version, mod_info.version_status, mod_info.version_source = "", "undeclared", "sandbox"
    else:
        value = result["version"]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            mod_info.version, mod_info.version_status, mod_info.version_source = "", "unresolved", ""
        elif str(value).strip():
            mod_info.version, mod_info.version_status, mod_info.version_source = str(value).strip(), "confirmed", "sandbox"
        else:
            mod_info.version, mod_info.version_status, mod_info.version_source = "", "undeclared", "sandbox"
    mod_info.full_sandbox_tried = True
    compatible = result.get("version_compatible")
    if compatible is None:
        mod_info.version_compatible, mod_info.version_compatible_status = "", "undeclared"
    elif isinstance(compatible, bool) or not isinstance(compatible, (str, int, float)):
        mod_info.version_compatible, mod_info.version_compatible_status = "", "unresolved"
    elif str(compatible).strip():
        mod_info.version_compatible, mod_info.version_compatible_status = str(compatible).strip(), "confirmed"
    else:
        mod_info.version_compatible, mod_info.version_compatible_status = "", "undeclared"
