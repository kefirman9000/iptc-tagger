"""AI markers: what in a file's metadata tells a stock site AI made or edited it, and taking Lightroom's
develop settings (where its AI masks live) out of a JPEG at save."""
import precheck
import tagger_core as core

MASK_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:Exposure2012="+0.30">
<crs:MaskGroupBasedCorrections><rdf:Seq><rdf:li><rdf:Description crs:What="Correction"
 crs:CorrectionName="RAW Review: Subject"><crs:CorrectionMasks><rdf:Seq>
<rdf:li crs:What="Mask/Image" crs:MaskSubType="1"/></rdf:Seq></crs:CorrectionMasks>
</rdf:Description></rdf:li></rdf:Seq></crs:MaskGroupBasedCorrections>
</rdf:Description></rdf:RDF></x:xmpmeta>"""


def _with_mask(exiftool, path, tmp_path):
    xmp = tmp_path / "mask.xmp"
    xmp.write_text(MASK_XMP, encoding="utf-8")
    exiftool._run(["-overwrite_original", "-tagsFromFile", str(xmp), "-XMP-crs:all",
                   "-Make=Sony", "-Model=ILCE-7M4", path])


def test_markers_from_metadata_rows():
    assert core.ai_markers({"XMP-dc:Title": "Red apple", "XMP-crs:Exposure2012": "+0.30"}) == []
    assert core.ai_markers({"XMP-crs:MaskGroupBasedCorrMaskWhat": "Mask/Image"})
    assert core.ai_markers({"XMP-crs:MaskGroupBasedCorrMaskWhat": ["Mask/Brush", "Mask/Gradient"]}) == []
    assert core.ai_markers({"XMP-crs:EnhanceDenoiseAlreadyApplied": "True"})
    assert core.ai_markers({"XMP-iptcExt:DigitalSourceType":
                            "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"})
    assert core.ai_markers({"XMP-iptcExt:DigitalSourceType": "Composite with trained algorithmic media"})
    assert core.ai_markers({"XMP-iptcExt:DigitalSourceType":
                            "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"}) == []
    assert core.ai_markers({"JUMBF:JUMDLabel": "c2pa"})
    assert core.ai_markers({"IFD0:Software": "Adobe Photoshop 26.0 (Generative Fill)"})
    assert core.ai_markers({"IFD0:Software": "Adobe Lightroom Classic 14.0"}) == []


def test_ai_mask_is_found_and_removed_at_save(exiftool, make_photos, tmp_path):
    path = make_photos(1)[0]
    _with_mask(exiftool, path, tmp_path)
    found = exiftool.read([path])[path]["ai_markers"]
    assert any("AI mask" in m for m in found)
    before = core.jpeg_image_data_hash(path)

    core.safe_write(exiftool, path, "Red apple on a wooden table", ["apple", "fruit"], strip_develop=True)

    meta = exiftool.read([path])[path]
    assert meta["ai_markers"] == []
    assert meta["title"] == "Red Apple on a Wooden Table" or meta["title"].lower() == "red apple on a wooden table"
    assert meta["keywords"] == ["apple", "fruit"]
    assert core.jpeg_image_data_hash(path) == before            # the picture itself is untouched
    left = exiftool._run(["-s", "-s", "-s", "-Make", "-Model", "-XMP-crs:all", path]).split()
    assert left[:2] == ["Sony", "ILCE-7M4"]                       # camera details stay
    assert "Mask/Image" not in left


def test_develop_settings_stay_without_the_option(exiftool, make_photos, tmp_path):
    path = make_photos(1)[0]
    _with_mask(exiftool, path, tmp_path)
    core.safe_write(exiftool, path, "Red apple", ["apple"])
    assert exiftool.read([path])[path]["ai_markers"]


def test_check_before_upload_lists_ai_markers():
    p = {"file": "a.jpg", "title": "Red apple on a wooden table", "description": "",
         "keywords": ["apple", "fruit", "red", "table", "wood", "food", "fresh", "healthy"],
         "editorial": False, "unsaved": False, "width": 6000, "height": 4000, "bytes": 5_000_000,
         "raw": False, "site_problems": [], "ai_markers": ["Has Content Credentials (C2PA), which can record AI tools"]}
    assert any(i["what"] == "AI markers" for i in precheck.check_photo(p, ["shutterstock"]))
    assert not any(i["what"] == "AI markers" for i in precheck.check_photo(dict(p, ai_markers=[]), ["shutterstock"]))
