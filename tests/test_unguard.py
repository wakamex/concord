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

    def test_port_definitions_become_inline_and_call_members(self):
        found = definitions(PORT, "INode")
        self.assertEqual(list(found), ["INode", "getTransformedBoundingBox"])
        constructor, box = found.values()
        self.assertTrue(constructor.startswith("inline INode::INode(INode* parent, IManager* manager) : Parent(parent)"))
        self.assertIn("AbsoluteTransformation.transformBox(box);", box)
        self.assertIn("box.repair();", box)

    def test_the_inline_block_follows_the_class_and_is_left_out_of_the_port(self):
        text = unguard(HEADER, list(definitions(PORT, "INode").values()), ("ox/scene/IAnimator.h",))
        block = text[text.index("#ifndef HARVEST_PORT") :]
        self.assertLess(text.index("};"), text.index("#ifndef HARVEST_PORT"))
        self.assertIn('#include "ox/scene/IAnimator.h"\n\nnamespace ox {\nnamespace scene {\n\ninline INode::INode(', block)
        self.assertNotIn("helper", block)
        self.assertTrue(block.rstrip().endswith("} // end namespace scene\n} // end namespace ox\n#endif\n\n#endif"))

    def test_a_header_without_port_definitions_is_only_exposed(self):
        self.assertEqual(unguard(HEADER, []), expose(HEADER))

    def test_irrlicht_members_are_written_as_definitions_after_the_class(self):
        original = (
            "namespace irr { namespace scene {\nclass ISceneNode : public IUnknown\n{\npublic:\n"
            "\tISceneNode(ISceneNode* parent, const core::vector3df& position = core::vector3df(0,0,0)) : Parent(parent) {}\n"
            "\tvirtual void setVisible(bool isVisible = true)\n\t{\n\t\tIsVisible = isVisible;\n\t}\n"
            "\tvirtual const core::vector3df getPosition() const\n\t{\n\t\treturn RelativeTranslation;\n\t}\n"
            "};\n} }\n"
        )
        found = irrlicht_definitions(original, "ISceneNode", "ISceneNode")
        self.assertEqual(list(found), ["ISceneNode", "setVisible", "getPosition"])
        self.assertEqual(found["ISceneNode"], "inline ISceneNode::ISceneNode(ISceneNode* parent, const ox::core::CVector3d<float>& position) : Parent(parent) {}")
        self.assertEqual(found["setVisible"], "inline void ISceneNode::setVisible(bool isVisible)\n{\n    IsVisible = isVisible;\n}")
        self.assertTrue(found["getPosition"].startswith("inline const ox::core::CVector3d<float> ISceneNode::getPosition() const\n{"))


if __name__ == "__main__":
    unittest.main()
