"""HollowKit のシーンレベル設定。"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup


def _sync_update(self, context):
    """設定変更時、アクティブオブジェクトのモディファイアへ即反映(ライブ更新)。"""
    from . import core
    core.sync_active(context)


def _hollow_update(self, context):
    """中空化に影響する設定の変更時: キャッシュを無効化してから反映する。"""
    from . import core
    core.clear_all_caches()
    core.sync_active(context)


class HollowKitSettings(PropertyGroup):
    # --- 処理対象 ---
    scope: EnumProperty(
        name="対象",
        items=[
            ('SELECTED', "選択", "選択中のメッシュオブジェクトを処理"),
            ('ALL', "全メッシュ", "シーン内の全メッシュオブジェクトを処理"),
        ],
        default='SELECTED',
    )

    # --- 中空化 ---
    use_hollow: BoolProperty(
        name="中空化する", default=True,
        description="モデルを指定した壁厚のシェル(殻)にする。"
                    "レジンやフィラメントの節約と反り防止になる",
        update=_hollow_update)
    wall_thickness: FloatProperty(
        name="壁厚",
        default=2.0, min=0.0, soft_max=20.0, precision=3, unit='LENGTH',
        description="シェルの厚み。光造形では 1.5〜3mm 程度が目安。"
                    "薄すぎると割れ、厚すぎると意味が無い",
        update=_hollow_update)

    voxel_mode: EnumProperty(
        name="解像度",
        items=[
            ('AUTO', "自動", "モデルの最大寸法 / ディテール でボクセルサイズを算出"),
            ('MANUAL', "手動", "絶対ボクセルサイズを指定"),
        ],
        default='AUTO',
        update=_hollow_update)
    detail: IntProperty(
        name="ディテール",
        default=128, min=8, soft_max=1024,
        description="内側の空洞面の細かさ(最大寸法に対するボクセル分割数)。"
                    "外側は元メッシュをそのまま使うため、この値を上げても"
                    "外観は変わらない。空洞面が粗く見えるときだけ上げる",
        update=_hollow_update)
    voxel_size: FloatProperty(
        name="ボクセルサイズ",
        default=0.5, min=1e-5, soft_max=5.0, precision=4, unit='LENGTH',
        description="内側の空洞面の解像度。小さいほど細かいが重い。"
                    "壁厚より十分小さくすること。外側の元メッシュには影響しない",
        update=_hollow_update)
    adaptivity: FloatProperty(
        name="滑らかさ(面削減)",
        default=0.0, min=0.0, max=1.0, precision=3,
        description="空洞面の平坦な部分の面をまとめてポリゴン数を減らす。"
                    "0 で最も忠実、上げるほど軽いが粗い。外側には影響しない",
        update=_hollow_update)

    cavity_mode: EnumProperty(
        name="空洞の選別",
        items=[
            ('LARGEST', "最大のみ",
             "最も大きい閉じた空洞だけを残し、他は中実のまま残す。"
             "小さな隙間空洞がレジン溜まりになるのを確実に防ぐ(推奨)"),
            ('THRESHOLD', "サイズ指定",
             "指定した径の球より大きい空洞をすべて残す。複数パーツを"
             "結合したメッシュなど、大きい空洞が複数あるときに使う"),
            ('ALL', "全て残す", "すべての空洞を残す(選別しない)"),
        ],
        default='LARGEST',
        update=_hollow_update)
    min_cavity_size: FloatProperty(
        name="最小空洞径",
        default=5.0, min=0.0, soft_max=50.0, precision=2, unit='LENGTH',
        description="この直径の球より体積が小さい閉じた空洞は、レジン溜まりに"
                    "なるため作らず中実のまま残す(サイズ指定モード)",
        update=_hollow_update)

    # --- 軸打ち用の中実柱 (キャッシュ後段なので固定中もライブ反映) ---
    solid_diameter: FloatProperty(
        name="軸柱の径",
        default=10.0, min=0.0, soft_max=50.0, precision=2, unit='LENGTH',
        description="軸マーカー位置に残す中実柱の直径。使う軸(真鍮線など)の"
                    "径より十分太くする(3mm 軸なら 8〜12mm 目安)",
        update=_sync_update)
    solid_length: FloatProperty(
        name="軸柱の長さ",
        default=20.0, min=0.0, soft_max=200.0, precision=2, unit='LENGTH',
        description="中実柱の長さ。マーカー位置から矢印方向へこの長さぶん"
                    "中身を残す。軸の差し込み深さより長くする",
        update=_sync_update)

    # --- 穴あけ ---
    use_holes: BoolProperty(
        name="穴あけする", default=True,
        description="穴マーカー(矢印)を置いた位置・向きに穴を掘り、"
                    "中空部を外へ通じさせる(レジン排出・エア抜き)",
        update=_sync_update)
    hole_diameter: FloatProperty(
        name="穴の径",
        default=3.0, min=0.0, soft_max=20.0, precision=3, unit='LENGTH',
        description="排出/エア抜き穴の直径。光造形では 2〜4mm が目安",
        update=_sync_update)
    hole_len_mode: EnumProperty(
        name="穴の長さ",
        items=[
            ('AUTO', "自動", "最大寸法の 2 倍(確実に貫通)"),
            ('MANUAL', "手動", "貫通長さを指定"),
        ],
        default='AUTO',
        update=_sync_update)
    hole_length: FloatProperty(
        name="長さ",
        default=100.0, min=0.0, soft_max=1000.0, precision=3, unit='LENGTH',
        description="ドリルの長さ。マーカー位置から矢印の方向へこの長さぶん"
                    "掘る。貫通させたい壁まで届く長さにする",
        update=_sync_update)
    use_fast_boolean: BoolProperty(
        name="高速ブーリアン(Manifold)", default=True,
        description="穴あけに高速で水密な Manifold ソルバーを使う。"
                    "高密度メッシュでも軽く、破れも出にくい。"
                    "モデルが水密(非多様体なし)でないと結果が消えるので、"
                    "その場合はオフにして従来の EXACT を使う",
        update=_sync_update)


def register():
    bpy.utils.register_class(HollowKitSettings)
    bpy.types.Scene.hollowkit = PointerProperty(type=HollowKitSettings)


def unregister():
    del bpy.types.Scene.hollowkit
    bpy.utils.unregister_class(HollowKitSettings)
