"""N パネル UI (View3D > サイドバー > HollowKit)。

ワークフローは 2 段階:
  ① 中空化(+軸打ち) → 「中空化を確定」
  ② 穴あけ → 「穴あけを確定」
"""

import bpy
from bpy.types import Panel

from . import core

_CATEGORY = "HollowKit"


class HOLLOWKIT_PT_main(Panel):
    bl_idname = "HOLLOWKIT_PT_main"
    bl_label = "HollowKit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY

    def draw(self, context):
        st = context.scene.hollowkit
        layout = self.layout
        obj = context.active_object
        is_mesh = (obj is not None and obj.type == 'MESH')
        has_hollow = is_mesh and core.get_hollow_modifier(obj) is not None
        has_drill = is_mesh and core.get_drill_modifier(obj) is not None

        layout.prop(st, "scope", expand=True)

        # ================= ① 中空化 + 軸打ち =================
        box = layout.box()
        box.label(text="① 中空化 + 軸打ち", icon='MOD_SOLIDIFY')

        col = box.column(align=True)
        col.scale_y = 1.2
        col.operator("hollowkit.apply",
                     text="中空化を更新" if has_hollow else "中空化を適用",
                     icon='MOD_SOLIDIFY')

        box.prop(st, "use_hollow")
        sub = box.column(align=True)
        sub.enabled = st.use_hollow
        sub.prop(st, "wall_thickness")
        sub.prop(st, "voxel_mode", expand=True)
        if st.voxel_mode == 'AUTO':
            sub.prop(st, "detail")
            if is_mesh:
                sub.label(text="ボクセル: {:.4g}".format(
                    core.resolve_voxel(st, obj)), icon='MOD_REMESH')
        else:
            sub.prop(st, "voxel_size")
        sub.prop(st, "adaptivity", slider=True)
        sub.prop(st, "cavity_mode", text="空洞")
        if st.cavity_mode == 'THRESHOLD':
            sub.prop(st, "min_cavity_size")

        # 軸打ち(中実柱)
        sub = box.column(align=True)
        sub.enabled = st.use_hollow
        sub.separator()
        sub.label(text="軸打ち(中実柱)", icon='EMPTY_AXIS')
        sub.prop(st, "solid_diameter")
        sub.prop(st, "solid_length")
        row = sub.row(align=True)
        row.enabled = is_mesh
        row.operator("hollowkit.add_solid", icon='EMPTY_SINGLE_ARROW')
        sub.label(text="Shift+右クリックでカーソルを表面に置いてから",
                  icon='CURSOR')
        if is_mesh:
            ns = core.count_solid_markers(obj)
            sub.label(text="軸マーカー: {} 個".format(ns),
                      icon='EMPTY_SINGLE_ARROW')

        # 空洞プレビュー / 固定
        pv_on = (has_hollow and core.get_preview(obj) is not None)
        row = box.row(align=True)
        row.enabled = has_hollow
        row.operator("hollowkit.cavity_preview",
                     text="プレビュー終了" if pv_on else "空洞プレビュー",
                     icon='HIDE_OFF', depress=pv_on)
        frozen = has_hollow and core.is_frozen(obj)
        row.operator("hollowkit.freeze", text="",
                     icon='FREEZE', depress=frozen)
        if pv_on:
            box.label(text="空洞と柱をワイヤ表示中", icon='INFO')
        if has_hollow and st.use_hollow and not frozen:
            box.label(text="未固定 — 重い場合は『中空化を更新』", icon='ERROR')

        row = box.row()
        row.enabled = has_hollow
        row.scale_y = 1.2
        row.operator("hollowkit.bake_hollow", icon='CHECKMARK')

        # ================= ② 穴あけ =================
        box = layout.box()
        box.label(text="② 穴あけ(排出/エア抜き)", icon='EMPTY_SINGLE_ARROW')
        sub = box.column(align=True)
        sub.prop(st, "hole_diameter")
        sub.prop(st, "hole_len_mode", expand=True)
        if st.hole_len_mode == 'MANUAL':
            sub.prop(st, "hole_length")
        row = sub.row(align=True)
        row.enabled = is_mesh
        row.operator("hollowkit.add_hole", icon='EMPTY_SINGLE_ARROW')
        sub.label(text="Shift+右クリックでカーソルを表面に置いてから",
                  icon='CURSOR')
        if is_mesh:
            n = core.count_markers(obj)
            sub.label(text="穴マーカー: {} 個".format(n),
                      icon='EMPTY_SINGLE_ARROW')
        if has_hollow:
            box.label(text="先に「中空化を確定」を推奨(軽くなる)", icon='INFO')
        if has_drill:
            box.label(text="マーカーは配置のみ(穴形状をワイヤ表示)", icon='INFO')
            box.label(text="実際の穴は「穴あけを確定」で開く", icon='INFO')
            dmod = core.get_drill_modifier(obj)
            box.prop(dmod, "show_viewport", text="穴をライブ表示(重い)")

        row = box.row()
        row.enabled = has_drill
        row.scale_y = 1.2
        row.operator("hollowkit.bake_drill", icon='CHECKMARK')

        # ================= 解除 =================
        layout.operator("hollowkit.clear", icon='X')


CLASSES = (
    HOLLOWKIT_PT_main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
