"""HollowKit のオペレーター(UI とコア処理の橋渡し)。

ワークフローは 2 段階:
  ① 中空化を適用 → 軸マーカーで中実柱を調整 → 「中空化を確定」で焼き込み
  ② 穴マーカーを配置(自動で穴あけモディファイアが付く) → 「穴あけを確定」
"""

import bpy
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector

from . import core


def _has_input(context):
    st = context.scene.hollowkit
    return bool(core.gather_objects(context, st.scope))


class HOLLOWKIT_OT_apply(Operator):
    bl_idname = "hollowkit.apply"
    bl_label = "中空化を適用 / 更新"
    bl_description = ("対象メッシュに中空化(段階①)の Geometry Nodes "
                     "モディファイアを付与(または更新)し、計算結果を固定"
                     "(キャッシュ)する。以後の軸マーカー調整は軽く反映される")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = core.gather_objects(context, st.scope)
        if not objs:
            self.report({'WARNING'}, "処理対象のメッシュがありません")
            return {'CANCELLED'}
        try:
            done = core.apply_to_objects(context, st, objs)
            # 段階分け: ここで中空化を一度だけ計算して固定(キャッシュ)する。
            if st.use_hollow:
                for obj in done:
                    core.freeze_object(context, obj)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.report({'ERROR'}, "HollowKit 失敗: {}".format(exc))
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            "HollowKit: {} オブジェクトに中空化を適用・固定しました。"
            "軸柱を調整したら「中空化を確定」へ".format(len(done)))
        return {'FINISHED'}


class _MarkerPlaceMixin:
    """ビュークリック位置(または 3D カーソル)へのマーカー配置の共通処理。"""

    # サブクラスで設定する
    _add_func = None          # core.add_*_marker
    _label = "マーカー"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def _place_from_mouse(self, context, event):
        """マウス下の表面にレイキャストして (位置, 法線) を返す。無ければ None。"""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        depsgraph = context.evaluated_depsgraph_get()
        result, location, normal, index, obj, matrix = context.scene.ray_cast(
            depsgraph, origin, direction)
        if not result:
            return None
        return location, normal

    def invoke(self, context, event):
        obj = context.active_object
        hit = self._place_from_mouse(context, event)
        if hit is not None:
            location, normal = hit
            type(self)._add_func(context, obj, location=location, normal=normal)
            self.report({'INFO'}, "{}を表面に追加しました".format(self._label))
            return {'FINISHED'}
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        # クリック位置が取れない場合は 3D カーソル位置に、モデル中心へ向けて置く。
        cursor = context.scene.cursor.location.copy()
        center = obj.matrix_world.translation.copy()
        outward = cursor - center
        type(self)._add_func(
            context, obj, location=cursor,
            normal=outward if outward.length > 1e-6 else Vector((0, 0, 1)))
        self.report({'INFO'},
                    "{}を 3D カーソル位置に追加しました".format(self._label))
        return {'FINISHED'}


class HOLLOWKIT_OT_add_hole(_MarkerPlaceMixin, Operator):
    bl_idname = "hollowkit.add_hole"
    bl_label = "穴マーカーを追加"
    bl_description = ("穴(排出/エア抜き)を掘る位置に矢印マーカーを追加する。"
                     "ビューでクリックした表面に置かれ、矢印の方向へ掘られる。"
                     "初回追加時に穴あけ(段階②)のモディファイアが自動で付く")
    bl_options = {'REGISTER', 'UNDO'}

    _add_func = staticmethod(core.add_hole_marker)
    _label = "穴マーカー"


class HOLLOWKIT_OT_add_solid(_MarkerPlaceMixin, Operator):
    bl_idname = "hollowkit.add_solid"
    bl_label = "軸マーカーを追加"
    bl_description = ("軸打ちしたい位置(ダボ面など)に矢印マーカーを追加する。"
                     "マーカーから矢印方向へ「軸柱の長さ」ぶん中実の柱が残り、"
                     "軸(真鍮線など)を差し込める。あとで移動・回転して調整できる")
    bl_options = {'REGISTER', 'UNDO'}

    _add_func = staticmethod(core.add_solid_marker)
    _label = "軸マーカー"


class HOLLOWKIT_OT_cavity_preview(Operator):
    bl_idname = "hollowkit.cavity_preview"
    bl_label = "空洞プレビュー"
    bl_description = ("空洞と軸柱の形をワイヤフレームで重ね表示する。"
                     "軸マーカーを動かすと即座に反映されるので、"
                     "結果を見ながら調整できる。もう一度押すと終了")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return core.has_hollow_modifier(context)

    def execute(self, context):
        obj = context.active_object
        st = context.scene.hollowkit
        if core.get_preview(obj) is not None:
            core.remove_preview(obj)
            self.report({'INFO'}, "空洞プレビューを終了しました")
            return {'FINISHED'}
        try:
            pv = core.create_preview(context, obj, st)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, "プレビュー失敗: {}".format(exc))
            return {'CANCELLED'}
        if pv is None:
            self.report({'WARNING'}, "中空化モディファイアがありません")
            return {'CANCELLED'}
        self.report({'INFO'},
                    "空洞プレビュー中 — マーカーを動かして確認できます")
        return {'FINISHED'}


class HOLLOWKIT_OT_freeze(Operator):
    bl_idname = "hollowkit.freeze"
    bl_label = "中空化を固定 / 解除"
    bl_description = ("重い空洞計算(SDF)の結果をキャッシュに固定し、以後の"
                     "軸柱調整を軽くする。「中空化を適用」時に自動で固定される"
                     "ので通常は不要。壁厚など中空化の設定を変えると解除される")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return core.has_hollow_modifier(context)

    def execute(self, context):
        obj = context.active_object
        if core.is_frozen(obj):
            core.unfreeze_object(obj)
            self.report({'INFO'}, "固定を解除しました(中空化を再計算します)")
            return {'FINISHED'}
        try:
            ok = core.freeze_object(context, obj)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, "固定失敗: {}".format(exc))
            return {'CANCELLED'}
        if not ok:
            self.report({'WARNING'}, "中空化モディファイアがありません")
            return {'CANCELLED'}
        self.report({'INFO'},
                    "中空化を固定しました — 軸マーカーの調整が軽くなります")
        return {'FINISHED'}


class HOLLOWKIT_OT_bake_hollow(Operator):
    bl_idname = "hollowkit.bake_hollow"
    bl_label = "中空化を確定"
    bl_description = ("段階①(中空化+軸柱)をメッシュへ焼き込む。以後は"
                     "壁厚などを変更できなくなる。確定後に穴マーカーを"
                     "配置すれば、穴あけ(段階②)は軽く行える")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = [o for o in core.gather_objects(context, st.scope)
                if core.get_hollow_modifier(o) is not None]
        if not objs:
            self.report({'WARNING'}, "中空化モディファイアを持つ対象がありません")
            return {'CANCELLED'}
        n = 0
        for obj in objs:
            try:
                if core.bake_hollow_object(context, obj):
                    n += 1
            except Exception as exc:  # noqa: BLE001
                self.report({'ERROR'}, "確定失敗 ({}): {}".format(obj.name, exc))
                return {'CANCELLED'}
        self.report({'INFO'},
                    "中空化を確定しました({} オブジェクト)。"
                    "次は穴マーカーを配置して「穴あけを確定」".format(n))
        return {'FINISHED'}


class HOLLOWKIT_OT_bake_drill(Operator):
    bl_idname = "hollowkit.bake_drill"
    bl_label = "穴あけを確定"
    bl_description = ("段階②(穴あけ)をメッシュへ焼き込み、穴マーカーを"
                     "片付ける。エクスポート前の最後の仕上げ")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = [o for o in core.gather_objects(context, st.scope)
                if core.get_drill_modifier(o) is not None]
        if not objs:
            self.report({'WARNING'}, "穴あけモディファイアを持つ対象がありません")
            return {'CANCELLED'}
        n = 0
        for obj in objs:
            try:
                if core.bake_drill_object(context, obj):
                    n += 1
            except Exception as exc:  # noqa: BLE001
                self.report({'ERROR'}, "確定失敗 ({}): {}".format(obj.name, exc))
                return {'CANCELLED'}
        self.report({'INFO'}, "穴あけを確定しました({} オブジェクト)".format(n))
        return {'FINISHED'}


class HOLLOWKIT_OT_clear(Operator):
    bl_idname = "hollowkit.clear"
    bl_label = "解除"
    bl_description = ("HollowKit のモディファイアとマーカーをすべて削除して"
                     "元の状態に戻す")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = core.gather_objects(context, st.scope)
        n = 0
        for obj in objs:
            if (core.get_hollow_modifier(obj) is not None
                    or core.get_drill_modifier(obj) is not None):
                n += 1
            core.remove_from_object(obj, remove_markers=True)
        self.report({'INFO'}, "HollowKit: {} オブジェクトを解除しました".format(n))
        return {'FINISHED'}


CLASSES = (
    HOLLOWKIT_OT_apply,
    HOLLOWKIT_OT_add_hole,
    HOLLOWKIT_OT_add_solid,
    HOLLOWKIT_OT_cavity_preview,
    HOLLOWKIT_OT_freeze,
    HOLLOWKIT_OT_bake_hollow,
    HOLLOWKIT_OT_bake_drill,
    HOLLOWKIT_OT_clear,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
