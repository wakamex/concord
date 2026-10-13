"""Exposing what a Harvest header keeps for the port to the matching build."""

import unittest

from concord.unguard import definitions, expose, irrlicht_definitions, unguard

HEADER = """#ifndef OX_SCENE_INODE_H
#define OX_SCENE_INODE_H

namespace ox {
namespace scene {

#ifdef HARVEST_PORT
class IManager;
#endif

class INode
{
public:
#ifdef HARVEST_PORT
    INode(INode* parent, IManager* manager);
#endif
    virtual void render() = 0;
#ifndef NDEBUG
    void check();
#endif

protected:
#ifdef HARVEST_PORT
    INode* Parent;
    IManager* Manager;
#else
    char Unrecovered[16];
#endif
    bool Visible;
};

} // end namespace scene
} // end namespace ox

#endif
"""

PORT = """#include "ox/scene/INode.h"
#include "scene/SceneMath.h"

namespace ox {
namespace scene {

INode::INode(INode* parent, IManager* manager) : Parent(parent), Manager(manager), Visible(true)
{
}

core::CAabbox3d<float> INode::getTransformedBoundingBox()
{
    core::CAabbox3d<float> box = getBoundingBox();
    daisy::scene::transformBox(AbsoluteTransformation, box);
    daisy::scene::repairBox(box);
    return box;
}

video::SMaterial& INode::getMaterial(int i)
{
    static video::SMaterial none;
    return none;
}

void helper()
{
}

} // end namespace scene
} // end namespace ox
"""


class Unguard(unittest.TestCase):
    def test_guarded_declarations_become_unconditional_and_the_stand_in_goes(self):
        text = expose(HEADER)
        self.assertNotIn("HARVEST_PORT", text)
        self.assertNotIn("Unrecovered", text)
        self.assertIn("class IManager;\n", text)
        self.assertIn("    INode* Parent;\n    IManager* Manager;\n    bool Visible;", text)

    def test_other_conditionals_are_kept(self):
        text = expose(HEADER)
        self.assertIn("#ifndef NDEBUG\n    void check();\n#endif\n", text)
        self.assertTrue(text.rstrip().endswith("#endif"))
        self.assertIn("#ifndef OX_SCENE_INODE_H", text)

    def test_port_definitions_give_bodies_that_call_members(self):
        found = definitions(PORT, "INode")
        self.assertEqual(list(found), ["INode", "getTransformedBoundingBox", "getMaterial"])
        self.assertEqual(found["INode"], " : Parent(parent), Manager(manager), Visible(true)\n{\n}")
        self.assertIn("AbsoluteTransformation.transformBox(box);", found["getTransformedBoundingBox"])
        self.assertIn("box.repair();", found["getTransformedBoundingBox"])

    def test_bodies_go_inside_the_class_so_it_has_no_key_function(self):
        bodies = {"INode": "\n    : Parent(parent)\n{\n    attach();\n}", "render": " { draw(); }", "missing": "{}"}
        text = unguard(HEADER.replace("virtual void render() = 0;", "virtual void render();"), bodies, ("ox/scene/IAnimator.h",))
        self.assertIn("    INode(INode* parent, IManager* manager)\n        : Parent(parent)\n    {\n        attach();\n    }\n", text)
        self.assertIn("    virtual void render() { draw(); }\n", text)
        self.assertNotIn("INode::", text)
        self.assertNotIn("HARVEST_PORT", text)

    def test_needed_headers_are_included_once(self):
        header = HEADER.replace("\nnamespace ox {", '\n#include "../IUnknown.h"\n\nnamespace ox {', 1)
        text = unguard(header, {}, ("ox/scene/IAnimator.h", "ox/IUnknown.h"))
        self.assertIn('#include "../IUnknown.h"\n#include "ox/scene/IAnimator.h"\n', text)
        self.assertEqual(text.count("IUnknown.h"), 1)

    def test_a_header_without_bodies_is_only_exposed(self):
        self.assertEqual(unguard(HEADER), expose(HEADER))

    def test_irrlicht_members_give_translated_bodies(self):
        original = (
            "namespace irr { namespace scene {\nclass ISceneNode : public IUnknown\n{\npublic:\n"
            "\tISceneNode(ISceneNode* parent, const core::vector3df& position = core::vector3df(0,0,0)) : Parent(parent) {}\n"
            "\tvirtual void setVisible(bool isVisible = true)\n\t{\n\t\tIsVisible = isVisible;\n\t}\n"
            "\tvirtual const core::vector3df getPosition() const\n\t{\n\t\tcore::vector3df p = RelativeTranslation;\n\t\treturn p;\n\t}\n"
            "};\n} }\n"
        )
        found = irrlicht_definitions(original, "ISceneNode")
        self.assertEqual(list(found), ["ISceneNode", "setVisible", "getPosition"])
        self.assertEqual(found["ISceneNode"], " : Parent(parent) {}")
        self.assertEqual(found["setVisible"], "\n\t{\n\t\tIsVisible = isVisible;\n\t}")
        self.assertIn("ox::core::CVector3d<float> p = RelativeTranslation;", found["getPosition"])


if __name__ == "__main__":
    unittest.main()
