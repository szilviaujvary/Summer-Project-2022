import torch


class MegaSAM(torch.optim.Optimizer):
    def __init__(self, params, base_optimizer, lr_M=0.01, rho=0.05, alpha=0.05, trace_penalty=True, **kwargs):
        if not rho >= 0.0:
            raise ValueError(f"Invalid rho, should be non-negative: {rho}")
        if not lr_M >= 0.0:
            raise ValueError(f"Invalid eta2, should be non-negative: {lr_M}")
        if not alpha >= 0.0:
            raise ValueError(f"Invalid rho, should be non-negative: {alpha}")

        self.trace_penalty = trace_penalty
        self.alpha = alpha
        self.rho = rho

        defaults = dict(lr_M=lr_M, **kwargs)
        super(MegaSAM, self).__init__(params, defaults)

        self.M_param_groups = []
        for param_group in self.param_groups:
            M_param_group = param_group.copy()
            M_param_group['params'] = [torch.ones_like(
                tensor, requires_grad=True) for tensor in param_group['params']]
            M_param_group['lr'] = M_param_group['lr_M']
            M_param_group.pop('lr_M')
            param_group.pop('lr_M')
            self.M_param_groups.append(M_param_group)

        self.base_optimizer = base_optimizer(
            self.param_groups + self.M_param_groups, **kwargs)

        self.eps = max(torch.finfo(
            self.param_groups[0]['params'][0].dtype).eps, 1e-12)

        self.shared_device = self.param_groups[0]["params"][0].device

    def mloss(self):
        squared_norm = self._grad_norm()
        return self.rho * torch.sqrt(squared_norm)

    def mpenalty(self):
        trace_Minv = torch.tensor(0.0, device=self.shared_device)
        logdet_M = torch.tensor(0.0, device=self.shared_device)
        for param_group, M_param_group in zip(self.param_groups, self.M_param_groups):
            for param, M in zip(param_group['params'], M_param_group['params']):
                if param.grad is None:
                    continue
                trace_Minv.add_((1/M).sum())
                logdet_M.add_(torch.log(M).sum())
        return self.alpha * trace_Minv + 1 * self.alpha * logdet_M

    @torch.no_grad()
    def first_step(self, zero_grad=False):
        squared_norm = self._grad_norm()
        scale = self.rho / (torch.sqrt(squared_norm) + self.eps)

        for param_group, M_param_group in zip(self.param_groups, self.M_param_groups):
            for param, M in zip(param_group['params'], M_param_group['params']):
                if param.grad is None:
                    continue
                self.state[param]["old_p"] = param.data.clone()
                param.add_(scale * param.grad / M)

        if zero_grad:
            self.zero_grad()

    @torch.no_grad()
    def second_step(self):
        for p in self.param_groups[0]["params"]:
            if p.grad is None:
                continue
            p.data = self.state[p]["old_p"]  # get back to "w" from "w + e(w)"

        # do the actual "sharpness-aware" update, this updates M as well.
        self.base_optimizer.step()

    @torch.no_grad()
    def step(self, closure):
        self._zero_M_grad()
        with torch.enable_grad():
            penalized_mloss = self.mloss() + self.mpenalty()
            penalized_mloss.backward()
        self.first_step(zero_grad=True)
        with torch.enable_grad():
            closure()
        self.second_step()

    def _grad_norm(self):
        squared_norm = torch.tensor(0.0, device=self.shared_device)
        for param_group, M_param_group in zip(self.param_groups, self.M_param_groups):
            for param, M in zip(param_group['params'], M_param_group['params']):
                if param.grad is None:
                    continue
                grad_tensor = param.grad.detach().to(self.shared_device)
                squared_norm.add_((grad_tensor**2/M).sum())

        return squared_norm

    def _zero_M_grad(self, set_to_none=False):
        for M_param_group in self.M_param_groups:
            for M in M_param_group['params']:
                if set_to_none:
                    M.grad = None
                else:
                    if M.grad is not None:
                        torch.zero_(M.grad)
