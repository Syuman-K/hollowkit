"""HollowKit — Blender add-on (extension).

光造形(SLA/DLP)プリンターでの出力向けに、モデルの中空化と排出/エア抜き
穴あけを行う。処理はすべて Geometry Nodes モディファイア(1 つのノード
グループ)で行うため非破壊で、いつでもパラメータを調整できる。

* 中空化: メッシュを SDF グリッド化 → 壁厚ぶん内側にオフセット →
  差集合 → メッシュ化、で指定した壁厚(mm)の水密シェルを作る。
* 穴あけ: 「穴マーカー」(Empty)を置いた位置・向きにシリンダーを差し引き、
  中空部を外へ通じさせてレジンを排出できるようにする。

Blender 5.0 拡張機能として登録する。詳細は blender_manifest.toml を参照。
"""

from . import operators, properties, ui


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()
