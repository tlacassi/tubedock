import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import safe_title, video_url

class UrlTests(unittest.TestCase):
    def test_accepts_external_urls_unchanged(self):
        for url in ['https://youtu.be/abcdefghijk?t=2', 'https://vimeo.com/123456', 'https://soundcloud.com/artist/track', 'https://www.youtube.com/watch?v=abcdefghijk&list=anything']:
            self.assertEqual(video_url(url), url)

    def test_rejects_invalid_and_obvious_local_urls(self):
        for url in ['http://localhost/test', 'http://127.0.0.1/test', 'http://192.168.1.1/test', 'http://[::1]/test', 'http://router.local/test', 'https://user@example.com/test', 'https://example.com:bad/test', 'file:///etc/passwd', 'https://example.com/\nheader', None]:
            self.assertIsNone(video_url(url))

class FilenameTests(unittest.TestCase):
    def test_preserves_words_and_removes_unsafe_characters(self):
        self.assertEqual(safe_title('  Été : vidéo / test ? 🎵  '), 'Été vidéo test')

    def test_reserved_empty_and_long_names(self):
        self.assertEqual(safe_title('CON.txt'), '_CON.txt')
        self.assertEqual(safe_title('...'), 'youtube-conversion')
        self.assertEqual(safe_title(None), 'youtube-conversion')
        self.assertLessEqual(len(safe_title('é' * 200).encode()), 180)

if __name__ == '__main__':
    unittest.main()
