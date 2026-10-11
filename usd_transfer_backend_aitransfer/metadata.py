from pxr import Usd, UsdGeom

def copy_root_metadata_animated(src_stage: Usd.Stage, dst_stage: Usd.Stage) -> None:
    src_root = src_stage.GetPseudoRoot()
    dst_root = dst_stage.GetPseudoRoot()
    if src_stage.GetStartTimeCode():
        dst_stage.SetStartTimeCode(src_stage.GetStartTimeCode())
    if src_stage.GetEndTimeCode():
        dst_stage.SetEndTimeCode(src_stage.GetEndTimeCode())
    if src_stage.GetTimeCodesPerSecond():
        dst_stage.SetTimeCodesPerSecond(src_stage.GetTimeCodesPerSecond())
    if src_stage.GetFramesPerSecond():
        dst_stage.SetFramesPerSecond(src_stage.GetFramesPerSecond())
    if src_root.HasAssetInfo():
        dst_root.SetAssetInfo(src_root.GetAssetInfo())

def copy_root_metadata(src_stage: Usd.Stage, dst_stage: Usd.Stage) -> None:
    src_root = src_stage.GetPseudoRoot()
    dst_root = dst_stage.GetPseudoRoot()
    if UsdGeom.GetStageUpAxis(src_stage):
        UsdGeom.SetStageUpAxis(dst_stage, UsdGeom.GetStageUpAxis(src_stage))
    if UsdGeom.GetStageMetersPerUnit(src_stage):
        UsdGeom.SetStageMetersPerUnit(dst_stage, UsdGeom.GetStageMetersPerUnit(src_stage))
    if not src_root:
        return
    if src_root.HasAssetInfo() and dst_root:
        dst_root.SetAssetInfo(src_root.GetAssetInfo())
