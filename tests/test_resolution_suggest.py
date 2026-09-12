import unittest
from resolution_suggest import WN_ResolutionSuggest


class ResolutionSuggestCompatibility(unittest.TestCase):
    def test_registered_seven_output_contract(self):
        result=WN_ResolutionSuggest().suggest(1920,1080,1024,'Longest Side',32,'round')
        self.assertEqual(result[:6],(1024,576,1920,1080,0.533333,'16:9'))
        self.assertEqual(len(WN_ResolutionSuggest.RETURN_TYPES),7)
        self.assertEqual(WN_ResolutionSuggest.RETURN_NAMES[-2:],('aspect_ratio','info'))

    def test_portrait_scale_and_snap_modes(self):
        node=WN_ResolutionSuggest()
        self.assertEqual(node.suggest(1080,1920,1024,'Longest Side',32,'round')[:2],(576,1024))
        self.assertEqual(node.suggest(100,200,50,'Scale Factor',8,'floor')[:2],(48,96))
        self.assertEqual(node.suggest(100,200,50,'Scale Factor',8,'ceil')[:2],(56,104))
