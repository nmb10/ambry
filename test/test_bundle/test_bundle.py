# -*- coding: utf-8 -*-

from test.test_base import TestBase


class Test(TestBase):

    def test_statemachine_states(self):

        db = self.new_database()

        b = self.new_bundle()

        b.state = 'first'
        b.state = 'second'

        db.commit()

        self.assertEquals('second', b.state)
        self.assertFalse(b.error_state)

        b.set_error_state()

        self.assertTrue(bool(b.error_state))
