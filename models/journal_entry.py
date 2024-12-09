# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import requests, json, base64
from datetime import datetime, date
from odoo.exceptions import ValidationError, UserError
import xmlrpc.client
from pytz import timezone
import logging
_logger = logging.getLogger(__name__)



class AccountMove(models.Model):
    
    _inherit = 'account.move'
    
    posted_to_remote = fields.Boolean("Posted to remote")
    
    @api.model
    def action_send_account_moves_to_remote_cron(self):
        # Find all account.move records that are not posted to remote
        records_to_send = self.search([('posted_to_remote', '=', False)])
        for rec in records_to_send:
            print("Processing record: ", rec.id)
            rec.send_account_moves_to_remote()
            rec.posted_to_remote = True
            print("Done processing record: ", rec.id)
    

    def send_account_moves_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()

        remote_type = config_parameters.get_param('remote_operations.remote_type')
        if remote_type != 'Branch Database':
            _logger.info("Database is not configured as 'Branch Database'. Skipping sending account moves to remote.")
            return
        
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy('{}/xmlrpc/2/common'.format(url))
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy('{}/xmlrpc/2/object'.format(url))

            # Get related account.move records
            # account_moves = self._get_related_account_moves()
            account_moves = self.env['account.move'].search([])
            for move in account_moves:
                if move.journal_id.dont_synchronize:
                    continue
                company_id = self._get_remote_id(models, db, uid, password, 'res.company', 'name', move.journal_id.company_id.name)
                move_data = self._prepare_move_data(models, db, uid, password, move, company_id)
                _logger.info("Account Move Data: %s", str(move_data))
                new_move = models.execute_kw(db, uid, password, 'account.move', 'create', [move_data])
                _logger.info("New Account Move: %s", str(new_move))

                # Post the new move
                models.execute_kw(db, uid, password, 'account.move', 'action_post', [[new_move]])
                _logger.info("Posted Account Move: %s", str(new_move))

            self.write({'posted_to_remote': True})

        except Exception as e:
            raise ValidationError("Error while sending account move data to remote server: {}".format(e))

    def _prepare_move_data(self, models, db, uid, password, move, company_id):
        move_lines = []
        for line in move.line_ids:
            account_to_check = line.account_id.code
            if line.account_id.substitute_account:
                account_to_check = line.account_id.substitute_account.code

            account_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_to_check)
            currency_id = self._get_remote_id_if_set(models, db, uid, password, 'res.currency', 'name', line.currency_id)
            
             # Prepare the analytic distribution
            # analytic_distribution = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)
            remote_analytic_account_id = self._prepare_analytic_distribution(models, db, uid, password, line.analytic_account_id)

            move_line_data = {
                'account_id': account_id,
                'name': line.name,
                'debit': line.debit,
                'credit': line.credit,
                'partner_id': self._get_remote_id_if_set(models, db, uid, password, 'res.partner', 'name', line.partner_id),
                'currency_id': currency_id,
                'amount_currency': line.amount_currency,
                # 'analytic_distribution': analytic_distribution,
                'analytic_distribution': {str(remote_analytic_account_id): 100} if remote_analytic_account_id else {},

            }

            move_lines.append((0, 0, move_line_data))
            branch_company_id = self._map_branch_to_remote_company(models, db, uid, password, move.branch_id)
            patient_char = move.patient_id.name if move.patient_id else None



        move_data = {
            'patient': patient_char,
            'company_id': branch_company_id,
            'ref': move.ref,
            'date': move.date,
            'move_type': move.move_type,
            'currency_id': currency_id,
            'journal_id': self._get_remote_id(models, db, uid, password, 'account.journal', 'name', move.journal_id.name),
            'line_ids': move_lines,
        }

        return move_data
    
    def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_account_id):
        remote_analytic_account_id = None
        
        if local_analytic_account_id:
            local_analytic_account = self.env['account.analytic.account'].browse(int(local_analytic_account_id.id))            
            remote_analytic_account_id = self._get_remote_id(
                models, db, uid, password,
                'account.analytic.account', 'name',
                local_analytic_account.name
            )
        
        return remote_analytic_account_id
    
    def _map_branch_to_remote_company(self, models, db, uid, password, branch_id):
        remote_company_id = None
        if branch_id:
            # Get the local company linked to the branch
            local_company = branch_id

            # Map to the remote company by name or another unique field
            remote_company_id = self._get_remote_id(
                models, db, uid, password,
                'res.company', 'name', local_company.name
            )
        return remote_company_id



    # def _prepare_analytic_distribution(self, models, db, uid, password, local_analytic_distribution):
    #     remote_analytic_distribution = {}
        
    #     if local_analytic_distribution:
    #         for local_analytic_account_id, distribution_percentage in local_analytic_distribution.items():
    #             local_analytic_account = self.env['account.analytic.account'].browse(int(local_analytic_account_id))
    #             remote_analytic_account_id = self._get_remote_id(models, db, uid, password, 'account.analytic.account', 'name', local_analytic_account.name)
    #             remote_analytic_distribution[str(remote_analytic_account_id)] = distribution_percentage

    #     return remote_analytic_distribution
    
    def _get_remote_company_id(self, models, db, uid, password):
        # Fetch the first company from the remote database
        remote_company = models.execute_kw(db, uid, password, 'res.company', 'search_read', [[]], {'fields': ['id'], 'limit': 1})
        if not remote_company:
            raise ValidationError(_("No company found in the remote database."))
        return remote_company[0]['id']

    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(db, uid, password, model, 'search_read', [[(field_name, '=', field_value)]], {'fields': ['id'], 'limit': 1})
        if not remote_record:
            raise ValidationError(_("The record for model '%s' with %s '%s' cannot be found in the remote database.") % (model, field_name, field_value))
        return remote_record[0]['id']

    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if field:
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        return False

    
    