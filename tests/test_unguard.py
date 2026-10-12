"""Exposing what a Harvest header keeps for the port to the matching build."""

import unittest

from concord.unguard import definitions, expose, unguard

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
        constructor, box = definitions(PORT, "INode")
        self.assertTrue(constructor.startswith("inline INode::INode(INode* parent, IManager* manager) : Parent(parent)"))
        self.assertIn("AbsoluteTransformation.transformBox(box);", box)
        self.assertIn("box.repair();", box)

    def test_the_inline_block_follows_the_class_and_is_left_out_of_the_port(self):
        text = unguard(HEADER, "INode", PORT, ("ox/scene/IAnimator.h",))
        block = text[text.index("#ifndef HARVEST_PORT") :]
        self.assertLess(text.index("};"), text.index("#ifndef HARVEST_PORT"))
        self.assertIn('#include "ox/scene/IAnimator.h"\n\nnamespace ox {\nnamespace scene {\n\ninline INode::INode(', block)
        self.assertNotIn("helper", block)
        self.assertTrue(block.rstrip().endswith("} // end namespace scene\n} // end namespace ox\n#endif\n\n#endif"))

    def test_a_header_without_port_definitions_is_only_exposed(self):
        self.assertEqual(unguard(HEADER, "INode"), expose(HEADER))


if __name__ == "__main__":
    unittest.main()
