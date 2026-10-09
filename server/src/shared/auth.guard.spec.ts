import { ExecutionContext, HttpException, HttpStatus } from '@nestjs/common';
import { sign } from 'jsonwebtoken';
import { AuthGuard } from './auth.guard';

const context = (headers: Record<string, string>) => {
  const request: any = { headers };
  const ctx = ({
    switchToHttp: () => ({ getRequest: () => request }),
  } as unknown) as ExecutionContext;
  return { ctx, request };
};

describe('AuthGuard', () => {
  const guard = new AuthGuard();

  beforeAll(() => {
    process.env.SECRET = 'secret-de-test';
  });

  it('refuse une requete sans en-tete d autorisation', async () => {
    const { ctx } = context({});
    await expect(guard.canActivate(ctx)).resolves.toBe(false);
  });

  it('refuse un schema autre que Bearer', async () => {
    const { ctx } = context({ autorization: 'Basic abc' });
    await expect(guard.canActivate(ctx)).rejects.toMatchObject({
      status: HttpStatus.UNAUTHORIZED,
    });
  });

  it('refuse un jeton signe avec un autre secret', async () => {
    const forged = sign({ id: '1', email: 'pirate@test.fr' }, 'autre-secret');
    const { ctx } = context({ autorization: `Bearer ${forged}` });
    await expect(guard.canActivate(ctx)).rejects.toBeInstanceOf(HttpException);
  });

  it('refuse un jeton expire', async () => {
    const expired = sign({ id: '1', email: 'a@test.fr' }, 'secret-de-test', {
      expiresIn: -10,
    });
    const { ctx } = context({ autorization: `Bearer ${expired}` });
    await expect(guard.canActivate(ctx)).rejects.toMatchObject({
      status: HttpStatus.UNAUTHORIZED,
    });
  });

  it('accepte un jeton valide et attache l utilisateur a la requete', async () => {
    const token = sign({ id: '42', email: 'karim@test.fr' }, 'secret-de-test');
    const { ctx, request } = context({ autorization: `Bearer ${token}` });
    await expect(guard.canActivate(ctx)).resolves.toBe(true);
    expect(request.user).toMatchObject({ id: '42', email: 'karim@test.fr' });
  });
});
