'''
(c) 2014 Brendan Bulik-Sullivan and Hilary Finucane

This module deals with getting all the data needed for LD Score regression from files
into memory and checking that the input makes sense. There is no math here. LD Score 
regression is implemented in the regressions module. 

'''
from __future__ import division
import numpy as np
import pandas as pd
import itertools as it
import scipy.stats as stats
import jackknife as jk
import parse as ps
import regressions as reg
import sys, traceback

# TODO these should be sets not dicts
# complementary bases
COMPLEMENT = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}
# bases
BASES = COMPLEMENT.keys()
# true iff strand ambiguous
STRAND_AMBIGUOUS = {''.join(x): x[0] == COMPLEMENT[x[1]] 
	for x in it.product(BASES,BASES) 
	if x[0] != x[1]}
# SNPS we want to keep
VALID_SNPS = {''.join(x)
	for x in it.product(BASES,BASES) 
	if x[0] != x[1] and not STRAND_AMBIGUOUS[''.join(x)]}
# True iff SNP 1 has the same alleles as SNP 2 (possibly w/ strand or ref allele flip)
MATCH_ALLELES = {''.join(x):
	((x[0] == x[2]) and (x[1] == x[3])) or # strand and ref match
	((x[0] == COMPLEMENT[x[2]]) and (x[1] == COMPLEMENT[x[3]])) or # ref match, strand flip
	((x[0] == x[3]) and (x[1] == x[2])) or # ref flip, strand match
	((x[0] == COMPLEMENT[x[3]]) and (x[1] == COMPLEMENT[x[2]])) # strand and ref flip
	for x in it.product(BASES,BASES,BASES,BASES)
	if (x[0] != x[1]) and (x[2] != x[3]) and 
	not STRAND_AMBIGUOUS[''.join(x[0:2])] and
	not STRAND_AMBIGUOUS[''.join(x[2:4])]}
# True iff SNP 1 has the same alleles as SNP 2 w/ ref allele flip (strand flip optional)
FLIP_ALLELES = {''.join(x):
	((x[0] == x[3]) and (x[1] == x[2])) or # strand match
	((x[0] == COMPLEMENT[x[3]]) and (x[1] == COMPLEMENT[x[2]])) # strand flip
	for x in it.product(BASES, BASES, BASES, BASES)
	if (x[0] != x[1]) and (x[2] != x[3]) and 
	(x[0] != COMPLEMENT[x[1]]) and (x[2] != COMPLEMENT[x[3]])
	and MATCH_ALLELES[''.join(x)]}
	
def _select_and_log(x, ii, log, msg):
	old_len = len(x)
	new_len = ii.sum()
	if new_len == 0:
		raise ValueError(msg.format(N=0))
	else:
		x = x[ii]
		log.log(msg.format(N=new_len))
		return x

def smart_merge(x, y):
	'''Check if SNP columns are equal. If so, save time by using concat instead of merge.'''
	if len(x) == len(y) and (x.SNP == y.SNP).all():
		x = x.reset_index(drop=True)
		y = y.reset_index(drop=True).drop('SNP', 1)
		out = pd.concat([x, y], axis=1)
	else:
		out = pd.merge(x, y, how='inner', on='SNP')
	
	return out
		
def _read_ref_ld(self, args, log):
	'''Read reference LD Scores.'''
	ref_ld = _read_chr_split_files(args.ref_ld_chr, args.ref_ld, log,
		 'reference panel LD Score', ps.ldscore)
	log.log('Read reference panel LD Scores for {N} SNPs.'.format(N=len(ref_ld)))
	return ref_ld

def _read_annot(self,args,log):
	'''Read annot matrix.'''	
	overlap_matrix, M_tot = _read_chr_split_files(args.ref_ld_chr, args.ref_ld, log, 
		'annot matrix', ps.annot, frqfile=args.frqfile)
	
	return overlap_matrix, M_tot

def _read_M(self, args, log):
	'''Read M (--M, --M-file, etc)'''
	if args.M:
		try:
			M_annot = [float(x) for x in args.M.split(',')]
		except TypeError as e:
			raise TypeError('Could not cast --M to float: ' + str(e.args))
	
		if len(M_annot) != len(ref_ld.columns) - 1:
			raise ValueError('# terms in --M must match # of LD Scores in --ref-ld.')
	
	### TODO add support for comma separated list of files
	# TODO make sure M_annot is a 2D array
	else:
		if args.ref_ld:
			M_annot = ps.M(args.ref_ld, common=args.not_M_5_50)	
		elif args.ref_ld_chr:
			M_annot = ps.M(args.ref_ld_chr, 22, common=args.not_M_5_50)

	return M_annot

def _read_w_ld(self, args, log):
	'''Read regression SNP LD.'''
	w_ld = _read_chr_split_files(args.w_ld_chr, args.w_ld, log, 
		'regression weight LD Score', ps.ldscore)
	if len(w_ld.columns) != 2:
		raise ValueError('--w-ld must point to a file with a single LD Score column.')

	w_ld.columns = ['SNP','LD_weights'] # prevent colname conflicts w/ ref ld
	log.log('Read regression weight LD Scores for {N} SNPs.'.format(N=len(w_ld)))
	return w_ld

def _read_chr_split_files(chr_arg, not_chr_arg, log, noun, parsefunc, *kwargs):
	'''Read files split across 22 chromosomes (annot, ref_ld, w_ld).'''
	try:
		if not_chr_arg:
			log.log('Reading {N} from {F} ...'.format(F=not_chr_arg, N=noun))
			out = parsefunc(not_chr_arg)
		elif chr_arg:
			f = ps.sub_chr(chr_arg, '[1-22]')
			log.log('Reading {N} from {F} ...'.format(F=f, N=noun))
			out = parsefunc(not_chr_arg, 22)
	except ValueError as e:
		log.log('Error parsing {N}.'.format(N=noun))
		raise e
	
	return out

def _parse_sumstats(self, args, log, fh, require_alleles=False, keep_na=False):
	'''Parse summary statistics.'''
	log.log('Reading summary statistics from {S} ...'.format(S=chisq))
	sumstats = ps.sumstats(chisq, require_alleles, keep_na, args.no_check)
	log_msg = 'Read summary statistics for {N} SNPs.'
	log.log(log_msg.format(N=len(sumstats)))
	if args.no_check:
		m = len(sumstats)
		sumstats = sumstats.drop_duplicates(subset='SNP')
		if m > len(sumstats):
			log.log('Dropped {M} SNPs with duplicated rs numbers.'.format(M=m-len(sumstats)))
		
	return sumstats

def _check_ld_condnum(self, args, log, M_annot, ref_ld):
	'''Check condition number of LD Score matrix.'''
	cond_num = int(np.linalg.cond(ref_ld))
	if cond_num > 100000:
		if args.invert_anyway:
			warn = "WARNING: LD Score matrix condition number is {C}. "
			warn += "Inverting anyway because the --invert-anyway flag is set."
			log.log(warn.format(C=cond_num))
		else:
			warn = "WARNING: LD Score matrix condition number is {C}. "
			warn += "Remove collinear LD Scores. "
			raise ValueError(warn.format(C=cond_num))

def _check_variance(self, log, M_annot, ref_ld):
	'''Remove zero-variance LD Scores.'''
	### TODO is there a SNP column here?
	ii = ref_ld.var(axis=0) == 0
	if ii.all():
		raise ValueError('All LD Scores have zero variance.')
	elif ii.any():
		log.log('Removing partitioned LD Scores with zero variance.')
		ref_ld = ref_ld.ix[:,~ii]
		M_annot = M_annot[:,~ii]

	return M_annot, ref_ld
		
def _warn_length(self, log, sumstats):
	if len(sumstats) < 200000:
		log.log('WARNING: number of SNPs less than 200k; this is almost always bad.')

def _print_cov(self, log, ldscore_reg, ofh):
	'''Prints covariance matrix of slopes.'''
	log.log('Printing covariance matrix of the estimates to {F}.'.format(F=ofh))
	np.savetxt(ofh, ldscore_reg.cat_cov)

def _print_delete_values(self, log, ldscore_reg, ofh):
	'''Prints block jackknife delete-k values'''
	log.log('Printing block jackknife delete-k values to {F}.'.format(F=ofh))
	np.savetxt(ofh, ldscore_reg.delete_values)
	
def _overlap_output(self, args, overlap_matrix, M_annot, n_annot, hsqhat, category_names, M_tot):
		### TODO what is happening here???
		for i in range(n_annot):
			overlap_matrix[i,:] = overlap_matrix[i,:]/M_annot
		
		prop_hsq_overlap = np.dot(overlap_matrix,hsqhat.prop_hsq.T).reshape((1,n_annot))
		prop_hsq_overlap_var = np.diag(np.dot(np.dot(overlap_matrix,hsqhat.prop_hsq_cov),overlap_matrix.T))
		prop_hsq_overlap_se = np.sqrt(prop_hsq_overlap_var).reshape((1,n_annot))
		one_d_convert = lambda x : np.array(x)[0]
		prop_M_overlap = M_annot/M_tot
		enrichment = prop_hsq_overlap/prop_M_overlap
		enrichment_se = prop_hsq_overlap_se/prop_M_overlap
		enrichment_p = stats.chi2.sf(one_d_convert((enrichment-1)/enrichment_se)**2, 1)
		df = pd.DataFrame({
			'Category':category_names,
			'Prop._SNPs':one_d_convert(prop_M_overlap),
			'Prop._h2':one_d_convert(prop_hsq_overlap),
			'Prop._h2_std_error': one_d_convert(prop_hsq_overlap_se),
			'Enrichment': one_d_convert(enrichment),
			'Enrichment_std_error': one_d_convert(enrichment_se),
			'Enrichment_p': enrichment_p
			})
		df = df[['Category','Prop._SNPs','Prop._h2','Prop._h2_std_error','Enrichment','Enrichment_std_error','Enrichment_p']]
		if args.print_coefficients:
			df['Coefficient'] = one_d_convert(hsqhat.coef)
			df['Coefficient_std_error'] = hsqhat.coef_se
			df['Coefficient_z-score'] = one_d_convert(hsqhat.coef/hsqhat.coef_se)

		df = df[np.logical_not(df['Prop._SNPs'] > .9999)]
		df.to_csv(args.out+'.results',sep="\t",index=False)	

def _merge_wrap(ld, sumstats, noun):
	'''Wrap smart merge with log messages about # of SNPs.'''
	sumstats = smart_merge(ref_ld, sumstats)
	msg = 'After merging with {L}, {N} SNPs remain.'
	if len(sumstats) == 0:
		raise ValueError(msg.format(N=len(sumstats), F=noun))
	else:
		log.log(msg.format(N=len(sumstats), F=noun))
	
	return sumstats

def _read_ld_sumstats(args, require_alleles=False, keep_na=False):
	sumstats = _parse_sumstats(args, log, pheno1, require_alleles=True, keep_na=True)	
	ref_ld = _read_ref_ld(args, log)
	M_annot = _read_M(args, log)
	M_annot, ref_ld = _check_variance(log, M_annot, ref_ld)
	w_ld = _read_w_ld(args, log)
	sumstats = _merge_wrap(ref_ld, sumstats, 'reference panel')
	sumstats = _merge_wrap(w_ld, sumstats, 'regression SNP')
	w_ld_cname = sumstats.columns[-1]
	ref_ld_cnames = ref_ld.columns[1:len(ref_ld.columns)]	
	return M_annot, w_ld_cname, ref_ld_cnames, sumstats


class H2(object):
	'''
	Implements h2 and partitioned h2 estimation.
	'''
	def __init__(self, args, header):
		M_annot, w_ld_cname, ref_ld_cnames, sumstats = _read_ld_sumstats(args, keep_na=False)
		ref_ld = sumstats.to_matrix(columns=ref_ld_cnames)
		_check_ld_condnum(args, log, ref_ld_cnames)
		_warn_length(log, sumstats)
		n_snp = len(sumstats); n_annot = len(ref_ld_cnames)
		s = lambda x: np.array(x).reshape((n_snp, 1))
		n_blocks = min(n_snp, args.n_blocks)
		hsqhat = jk.Hsq(s(sumstats.CHISQ), ref_ld, s(sumstats[w_ld_cname]), s(sumstats.N), 
			M_annot, n_blocks=args.n_blocks, intercept=args.constrain_intercept)

		if args.print_cov:
			_print_cov(args, log, hsqhat, n_annot)
		if args.print_delete_vals:
			_print_delete_values(args, log, hsqhat)	
		if args.overlap_annot:
			overlap_matrix, M_tot = _read_annot(args, log)
			_overlap_output(args, overlap_matrix, M_annot, n_annot, hsqhat, ref_ld_cnames, M_tot)

		log.log(hsqhat.summary(ref_ld_cnames, args.overlap_annot, args.out))			


class Rg(object):
	'''
	Implements rg estimation with fixed LD Scores, one fixed phenotype, and a loop over
	a list (possibly with length one) of other phenotypes.
	
	'''
	def __init__(self, args, log):		
		rg_paths, rg_files = self._parse_rg(args.rg)
		pheno1 = rg_paths[0]
		out_prefix = args.out + rg_files[0]
		M_annot, w_ld_cname, ref_ld_cnames, sumstats	= _read_ld_sumstats(args, require_alleles=True, keep_na=True)
		RG = []
		for i, pheno2 in enumerate(rg_paths[1:len(rg_paths)]):
			log.log('Computing genetic correlation for phenotype {I}/{N}'.format(I=i+2, N=len(rg_paths)))	
			try:
				sumstats2 = _parse_sumstats(args, log, pheno2, require_alleles=True, keep_na=True)
				out_prefix_loop = out_prefix + '_' + rg_files[i+1]
				sumstats_loop = _merge_sumstats_sumstats(args, sumstats, sumstats2, log) # NAs removed
				_check_ld_condnum(args, log, M_annot, sumstats_loop[ref_ld_cnames])
				_warn_length(log, sumstats_loop)
				rghat = self._rg(sumstats_loop, args, log, M_annot, ref_ld_cnames, w_ld_cname)	
				self._print_gencor(args, log, rghat, ref_ld_cnames, i, rg_paths, i==0)
				RG.append(rghat)	
				if args.print_cov:
					self.print_cov(rghat, out_prefix_loop, log)		
				if args.print_delete_vals:
					self._print_delete_vals(rghat, out_prefix_loop, args, log, n_annot)
					
			except Exception as e: # keep going if phenotype 50/100 causes an error
				msg = 'ERROR computing rg for phenotype {I}/{N}, from file {F}.'
				log.log(msg.format(I=i+2, N=len(rg_paths), F=rg_paths[i+1]))
				ex_type, ex, tb = sys.exc_info()
				log.log( traceback.format_exc(ex)+'\n' )
				RG.append(None) 
				
		log.log('Summary of Genetic Correlation Results')
		log.log(pd.DataFrame({
			'p1': [rg_paths[0] for i in xrange(1,len(rg_paths))],
			'p2': rg_paths[1:len(rg_paths)],
			'rg': [x.rg if x is not None else 'NA' for x in RG],
			'se': [x.rg_se if x is not None else 'NA'for x in RG],
			'z': [x.z if x is not None else 'NA' for x in RG],
			'p': [x.p if x is not None else 'NA' for x in RG]
			}).to_string(header=True, index=False)+'\n')
			
	def _print_gencor(self, args, log, rghat, ref_ld_cnames,i, rg_paths, print_hsq1):
		l = ''.join(['-' for i in xrange(28)])
		if print_hsq1:
			log.log('\nHeritability of phenotype 1\n' + l)
			log.log(rghat.hsq1.summary(ref_ld_cnames, args.overlap_annot))

		log.log('\nHeritability of phenotype {I}/{N}'.format(I=i+2, N=len(rg_paths)))
		log.log(''.join(['-' for i in xrange(len(msg)) ]))
		log.log(rghat.hsq2.summary(ref_ld_cnames, args.overlap_annot))
		log.log('\nGenetic Covariance\n' + l)
		log.log(rghat.gencov.summary(ref_ld_cnames, args.overlap_annot))
		log.log('\nGenetic Correlation\n' + l)
		log.log(rghat.summary()+'\n')

	def _merge_sumstats_sumstats(self, args, sumstats1, sumstats2, log):
		'''
		Merge two sets of summary statistics and align strand + reference alleles.
		This function filters out NA's
				
		'''
		sumstats2.rename(columns={'N':'N1','BETA':'BETA1'}, inplace=True)			
		sumstats2.rename(columns={'A1':'A1x','A2':'A2x','N':'N2','BETA':'BETA2'}, inplace=True)			
		x = merge_wrap(sumstats1, sumstats2, 'summary staistics')
		ii = x.BETA.notnull() & x.BETAx.notnull()
		x = _select_and_log(x, ii, '{N} SNPs with nonmissing values.') 	
 		# remove bad variants (mismatched alleles, non-SNPs, strand ambiguous)
 		alleles = x.A1+x.A2+x.A1x+x.A2x
 		if not args.no_check_alleles:
 			ii = alleles.apply(lambda y: y in VALID_SNPS)
			x = _select_and_log(x, ii, '{N} SNPs with valid alleles.')
		
		# align beta1 and beta2 to same choice of ref allele (allowing for strand flip)
		x['BETA2'] *= (-1)**alleles.apply(lambda y: FLIP_ALLELES[y])
		x.drop(['A1','A2','A1x','A2x'], axis=1, inplace=True)
		return x
	
	def _rg(self, sumstats, args, log, M_annot, ref_ld_cnames, w_ld_cname):
		'''Run the regressions.'''
		n_snp = len(sumstats); n_annot = len(ref_ld_cnames)
		s = lambda x: np.array(x).reshape((n_snp, 1))
		n_blocks = min(args.n_blocks, n_snp)	
		ref_ld = sumstats.as_matrix(columns=ref_ld_cnames) # TODO is this the right shape?
		intercepts = [None, None, None]
		if args.constrain_intercept is not None:
			intercepts = args.constrain_intercept

		rghat = reg.RG(s(sumstats.Z1), s(sumstats.Z2), ref_ld, s(sumstats[w_ld_cname]), 
			s(sumstats.N1), s(sumstats.N2), M_annot, intercept_hsq1=intercepts[0], 
			intercept_hsq2=intercepts[1], intercepts_gencov=intercepts[2], n_blocks=n_blocks) 
		
		return rghat
	
	def _parse_rg(self, rg):
		'''Parse args.rg.'''
		rg_paths = args.rg.split(',')	
		rg_files = [x.split('/')[-1] for x in rg_paths]
		if len(rg_paths) < 2:
			raise ValueError('Must specify at least two phenotypes for rg estimation.')
		
		return rg_paths, rg_files
	
	def _print_delete_vals(self, rg, fh, log):
		'''Print block jackknife delete values.'''
		_print_delete_values(rghat.hsq1, '.hsq1.delete_k', log)
		_print_delete_values(rghat.hsq2, fh+'.hsq2.delete_k', log)
		_print_delete_values(rghat.gencov, fh+'.gencov.delete_k', log)

	def _print_cov(self, rghat, fh, args, log, n_annot):
		'''Print covariance matrix of estimates.'''		
		_print_cov(args, log, rghat.hsq1, n_annot, out_prefix_loop+'.hsq2.cov')
		_print_cov(args, log, rghat.hsq2, n_annot, out_prefix_loop+'.hsq2.cov')
		_print_gencov_cov(args, log, rghat.gencov, n_annot, out_prefix_loop+'.gencov.cov')