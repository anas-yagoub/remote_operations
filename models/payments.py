# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class AccountPayments(models.Model):
    _inherit = 'account.payment'

    matching_no = fields.Char(string='#Matching Number Custom')
